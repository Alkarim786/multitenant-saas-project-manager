import { DndContext, DragEndEvent, DragOverlay, DragStartEvent, PointerSensor, closestCorners, useDroppable, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, accessToken, api, currentTenant } from "./api";
import { Column, Issue, applyIssue, applyPlacement, findIssue, placementFor, removeIssue } from "./boardState";

interface Member { id: number; email: string; name: string }
interface Comment { id: number; author: Member | null; body: string; created_at: string }
type Live = "connecting" | "live" | "offline";

const columnDropId = (id: number) => `column-${id}`;

function Card({ issue, members, onOpen, disabled }: { issue: Issue; members: Member[]; onOpen: () => void; disabled: boolean }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: issue.id, disabled });
  const assignee = members.find((m) => m.id === issue.assignee);
  return (
    <div
      ref={setNodeRef}
      className={`card${isDragging ? " dragging" : ""}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      data-testid={`card-${issue.key}`}
      onClick={onOpen}
      {...attributes}
      {...listeners}
    >
      <CardBody issue={issue} assignee={assignee} />
    </div>
  );
}

function CardBody({ issue, assignee }: { issue: Issue; assignee?: Member }) {
  return (
    <>
      <div className="card-title">{issue.title}</div>
      <div className="card-meta">
        <span className="mono">{issue.key}</span>
        <span className={`priority p-${issue.priority.toLowerCase()}`}>{issue.priority.toLowerCase()}</span>
        {issue.story_points !== null && <span className="points">{issue.story_points} pt</span>}
        {assignee && <span className="avatar" title={assignee.name}>{assignee.name.slice(0, 2).toUpperCase()}</span>}
      </div>
    </>
  );
}

function BoardColumn({ column, members, canWrite, onOpen, onAdd }: {
  column: Column; members: Member[]; canWrite: boolean; onOpen: (i: Issue) => void; onAdd: (columnId: number, title: string) => Promise<void>;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: columnDropId(column.id) });
  const [title, setTitle] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    await onAdd(column.id, title.trim());
    setTitle("");
  }

  return (
    <section className={`column${isOver ? " over" : ""}`}>
      <header><h3>{column.name}</h3><span className="count">{column.issues.length}</span></header>
      <div ref={setNodeRef} className="cards">
        <SortableContext items={column.issues.map((i) => i.id)} strategy={verticalListSortingStrategy}>
          {column.issues.map((i) => <Card key={i.id} issue={i} members={members} onOpen={() => onOpen(i)} disabled={!canWrite} />)}
        </SortableContext>
      </div>
      {canWrite && (
        <form onSubmit={submit} className="add">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add an issue" aria-label={`Add an issue to ${column.name}`} />
        </form>
      )}
    </section>
  );
}

function IssuePanel({ issue, members, canWrite, onClose, onChanged, onDeleted, notify }: {
  issue: Issue; members: Member[]; canWrite: boolean; onClose: () => void;
  onChanged: (i: Issue) => void; onDeleted: (id: number) => void; notify: (m: string) => void;
}) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [body, setBody] = useState("");

  useEffect(() => {
    api<Comment[]>(`/api/issues/${issue.id}/comments/`).then(setComments).catch((e) => notify(e.message));
  }, [issue.id, notify]);

  async function patch(fields: Partial<Issue>) {
    try {
      onChanged(await api<Issue>(`/api/issues/${issue.id}/`, "PATCH", { ...fields, expected_version: issue.version }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        onChanged(e.body.current);
        notify("Someone else changed this issue. It now shows their version.");
      } else notify((e as Error).message);
    }
  }

  async function addComment(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    try {
      const c = await api<Comment>(`/api/issues/${issue.id}/comments/`, "POST", { body });
      setComments((cs) => [...cs, c]);
      setBody("");
    } catch (err) { notify((err as Error).message); }
  }

  async function remove() {
    try {
      await api(`/api/issues/${issue.id}/`, "DELETE");
      onDeleted(issue.id);
      onClose();
    } catch (err) { notify((err as Error).message); }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <aside className="panel" role="dialog" aria-label={issue.key} onClick={(e) => e.stopPropagation()}>
        <header className="panel-head">
          <span className="mono">{issue.key}</span>
          <button className="ghost" onClick={onClose}>Close</button>
        </header>
        <input
          key={`title-${issue.version}`} className="panel-title" defaultValue={issue.title} disabled={!canWrite} aria-label="Title"
          onBlur={(e) => { if (e.target.value.trim() && e.target.value !== issue.title) patch({ title: e.target.value.trim() }); }}
        />
        <label>Description
          <textarea
            key={`desc-${issue.version}`} rows={4} defaultValue={issue.description} disabled={!canWrite}
            onBlur={(e) => { if (e.target.value !== issue.description) patch({ description: e.target.value }); }}
          />
        </label>
        <div className="fields">
          <label>Priority
            <select value={issue.priority} disabled={!canWrite} onChange={(e) => patch({ priority: e.target.value as Issue["priority"] })}>
              {["LOW", "MEDIUM", "HIGH", "URGENT"].map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label>Assignee
            <select value={issue.assignee ?? ""} disabled={!canWrite} onChange={(e) => patch({ assignee: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Unassigned</option>
              {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>
          <label>Due date
            <input type="date" value={issue.due_date ?? ""} disabled={!canWrite} onChange={(e) => patch({ due_date: e.target.value || null })} />
          </label>
          <label>Points
            <input
              key={`pts-${issue.version}`} type="number" min={0} defaultValue={issue.story_points ?? ""} disabled={!canWrite}
              onBlur={(e) => { const v = e.target.value === "" ? null : Number(e.target.value); if (v !== issue.story_points) patch({ story_points: v }); }}
            />
          </label>
        </div>
        <h4>Comments</h4>
        <ul className="comments">
          {comments.map((c) => <li key={c.id}><strong>{c.author?.name ?? "Former member"}</strong><p>{c.body}</p></li>)}
          {comments.length === 0 && <li className="muted">No comments yet.</li>}
        </ul>
        {canWrite && (
          <form onSubmit={addComment} className="comment-form">
            <input value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a comment" aria-label="Write a comment" />
            <button>Send</button>
          </form>
        )}
        {canWrite && <button className="danger" onClick={remove}>Delete issue</button>}
      </aside>
    </div>
  );
}

export function Board({ projectId, role, notify }: { projectId: number; role: string; notify: (m: string) => void }) {
  const [columns, setColumns] = useState<Column[] | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [exporting, setExporting] = useState(false);
  const canWrite = role !== "VIEWER";
  const columnsRef = useRef<Column[]>([]);
  columnsRef.current = columns ?? [];

  const load = useCallback(async () => {
    const b = await api(`/api/projects/${projectId}/board/`);
    setColumns(b.columns);
    setMembers(b.members);
  }, [projectId]);

  useEffect(() => { load().catch((e) => notify(e.message)); }, [load, notify]);

  // Live updates. Events missed while disconnected are recovered by reloading on reconnect.
  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let timer: number | undefined;

    function connect() {
      setLive("connecting");
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/ws/boards/${projectId}/?token=${accessToken()}&tenant=${currentTenant()}`);
      socket.onopen = () => {
        if (retry > 0) load().catch(() => undefined);
        retry = 0;
        setLive("live");
      };
      socket.onmessage = (e) => {
        const { event, payload } = JSON.parse(e.data);
        if (event === "issue.deleted") setColumns((c) => c && removeIssue(c, payload.id));
        else if (event.startsWith("issue.")) setColumns((c) => c && applyIssue(c, payload));
      };
      socket.onclose = () => {
        if (closed) return;
        setLive("offline");
        retry += 1;
        timer = window.setTimeout(connect, Math.min(1000 * 2 ** retry, 15000));
      };
    }

    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, [projectId, load]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function onDragStart(e: DragStartEvent) { setActiveId(Number(e.active.id)); }

  async function onDragEnd(e: DragEndEvent) {
    setActiveId(null);
    const current = columnsRef.current;
    if (!e.over) return;
    const id = Number(e.active.id);
    const overRaw = e.over.id;
    let overId: number | null = null;
    let overColumn: number;
    if (typeof overRaw === "string") {
      overColumn = Number(overRaw.replace("column-", ""));
    } else {
      overId = overRaw;
      const owner = current.find((c) => c.issues.some((i) => i.id === overId));
      if (!owner) return;
      overColumn = owner.id;
    }
    const placement = placementFor(current, id, overId, overColumn);
    const issue = findIssue(current, id);
    if (!placement || !issue) return;

    setColumns(applyPlacement(current, id, placement));
    try {
      const moved = await api<Issue>(`/api/issues/${id}/move/`, "POST", { ...placement, version: issue.version });
      setColumns((c) => c && applyIssue(c, moved));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) notify("Someone else moved this card first. The board shows the latest order.");
      else notify((err as Error).message);
      await load().catch(() => undefined);
    }
  }

  async function addIssue(columnId: number, title: string) {
    try {
      const created = await api<Issue>("/api/issues/", "POST", { project: projectId, column: columnId, title });
      setColumns((c) => c && applyIssue(c, created));
    } catch (e) { notify((e as Error).message); }
  }

  async function exportCsv() {
    setExporting(true);
    try {
      const job = await api(`/api/projects/${projectId}/export/`, "POST");
      for (let i = 0; i < 30; i++) {
        const state = await api(`/api/exports/${job.id}/`);
        if (state.status === "DONE") {
          const csv = await api<string>(`/api/exports/${job.id}/download/`);
          const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
          const a = document.createElement("a");
          a.href = url;
          a.download = `export-${job.id}.csv`;
          a.click();
          URL.revokeObjectURL(url);
          notify("Export ready.");
          return;
        }
        if (state.status === "FAILED") throw new Error(state.error || "Export failed");
        await new Promise((r) => setTimeout(r, 500));
      }
      throw new Error("The export is taking longer than expected.");
    } catch (e) { notify((e as Error).message); } finally { setExporting(false); }
  }

  if (!columns) return <p className="muted pad">Loading board...</p>;
  const active = activeId === null ? undefined : findIssue(columns, activeId);
  const open = openId === null ? undefined : findIssue(columns, openId);

  return (
    <div className="board-wrap">
      <div className="board-bar">
        <span className={`live status-${live}`} data-testid="live">{live === "live" ? "Live" : live === "connecting" ? "Connecting" : "Offline, reconnecting"}</span>
        {canWrite && <button className="ghost" onClick={exportCsv} disabled={exporting}>{exporting ? "Exporting..." : "Export CSV"}</button>}
      </div>
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setActiveId(null)}>
        <div className="board">
          {columns.map((c) => (
            <BoardColumn key={c.id} column={c} members={members} canWrite={canWrite} onOpen={(i) => setOpenId(i.id)} onAdd={addIssue} />
          ))}
        </div>
        <DragOverlay>{active && <div className="card overlay"><CardBody issue={active} assignee={members.find((m) => m.id === active.assignee)} /></div>}</DragOverlay>
      </DndContext>
      {open && (
        <IssuePanel
          issue={open} members={members} canWrite={canWrite} notify={notify}
          onClose={() => setOpenId(null)}
          onChanged={(i) => setColumns((c) => c && applyIssue(c, i))}
          onDeleted={(id) => setColumns((c) => c && removeIssue(c, id))}
        />
      )}
    </div>
  );
}
