/** Pure board state logic, kept apart from React so it can be unit tested. */

export interface Issue {
  id: number;
  key: string;
  project: number;
  column: number;
  number: number;
  title: string;
  description: string;
  assignee: number | null;
  priority: "LOW" | "MEDIUM" | "HIGH" | "URGENT";
  story_points: number | null;
  due_date: string | null;
  rank: string;
  version: number;
}

export interface Column {
  id: number;
  name: string;
  position: number;
  issues: Issue[];
}

/** Bytewise comparison, matching the "C" collation the server sorts ranks with. */
function byRank(a: Issue, b: Issue): number {
  if (a.rank === b.rank) return a.id - b.id;
  return a.rank < b.rank ? -1 : 1;
}

/** Insert or update an issue from the server, keeping every column in rank order. */
export function applyIssue(columns: Column[], issue: Issue): Column[] {
  // A delayed broadcast must not undo a newer state we already have.
  const known = findIssue(columns, issue.id);
  if (known && known.version > issue.version) return columns;
  return columns.map((c) => {
    const rest = c.issues.filter((i) => i.id !== issue.id);
    if (c.id !== issue.column) {
      return rest.length === c.issues.length ? c : { ...c, issues: rest };
    }
    return { ...c, issues: [...rest, issue].sort(byRank) };
  });
}

export function removeIssue(columns: Column[], id: number): Column[] {
  return columns.map((c) => (c.issues.some((i) => i.id === id) ? { ...c, issues: c.issues.filter((i) => i.id !== id) } : c));
}

export function findIssue(columns: Column[], id: number): Issue | undefined {
  for (const c of columns) {
    const hit = c.issues.find((i) => i.id === id);
    if (hit) return hit;
  }
  return undefined;
}

export interface Placement {
  column: number;
  before: number | null;
  after: number | null;
}

/**
 * Where a card lands when dropped on another card or on an empty part of a column.
 * Dragging down places it below the card under the pointer, dragging up places it above.
 * Returns null if the drop changes nothing.
 */
export function placementFor(columns: Column[], activeId: number, overId: number | null, overColumnId: number): Placement | null {
  const target = columns.find((c) => c.id === overColumnId);
  if (!target) return null;
  const others = target.issues.filter((i) => i.id !== activeId);
  const sameColumn = others.length !== target.issues.length;
  const from = target.issues.findIndex((i) => i.id === activeId);

  let index = others.length;
  if (overId !== null && overId !== activeId) {
    const overInOthers = others.findIndex((i) => i.id === overId);
    if (overInOthers === -1) return null;
    const overInTarget = target.issues.findIndex((i) => i.id === overId);
    index = sameColumn && from < overInTarget ? overInOthers + 1 : overInOthers;
  } else if (overId === activeId) {
    return null;
  }
  if (sameColumn && index === from) return null;
  return {
    column: overColumnId,
    before: index > 0 ? others[index - 1].id : null,
    after: index < others.length ? others[index].id : null,
  };
}

/** Apply a placement locally before the server answers, so the drag feels instant. */
export function applyPlacement(columns: Column[], activeId: number, p: Placement): Column[] {
  const issue = findIssue(columns, activeId);
  if (!issue) return columns;
  const moved = { ...issue, column: p.column };
  return columns.map((c) => {
    const rest = c.issues.filter((i) => i.id !== activeId);
    if (c.id !== p.column) return rest.length === c.issues.length ? c : { ...c, issues: rest };
    const at = p.before !== null ? rest.findIndex((i) => i.id === p.before) + 1 : 0;
    return { ...c, issues: [...rest.slice(0, at), moved, ...rest.slice(at)] };
  });
}
