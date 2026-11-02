import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, hasSession, logout, saveTokens, setTenant } from "./api";
import { Board } from "./Board";

interface Org { id: number; name: string; slug: string; role: string }
interface Project { id: number; key: string; name: string }
type View = { kind: "board"; project: number } | { kind: "members" } | { kind: "audit" };

const isAdmin = (role: string) => role === "OWNER" || role === "ADMIN";

function AuthScreen({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", organisation: "" });
  const [error, setError] = useState("");
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const r = mode === "login"
        ? await api("/api/auth/token/", "POST", { email: form.email, password: form.password })
        : await api("/api/auth/register/", "POST", form);
      saveTokens(r.access, r.refresh);
      onDone();
    } catch (err) { setError((err as Error).message); }
  }

  return (
    <main className="auth">
      <form onSubmit={submit} className="auth-card">
        <h1>Projectboard</h1>
        <p className="muted">{mode === "login" ? "Sign in to your organisation" : "Create an account and organisation"}</p>
        {mode === "register" && (
          <>
            <label>Your name<input required value={form.name} onChange={set("name")} /></label>
            <label>Organisation<input required value={form.organisation} onChange={set("organisation")} /></label>
          </>
        )}
        <label>Email<input required type="email" autoComplete="username" value={form.email} onChange={set("email")} /></label>
        <label>Password<input required type="password" minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"} value={form.password} onChange={set("password")} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button>{mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New here? Create an organisation" : "Already have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}

function Members({ role, notify }: { role: string; notify: (m: string) => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState("MEMBER");
  const canManage = isAdmin(role);
  const load = useCallback(() => api("/api/members/").then((r) => setRows(r.results)).catch((e) => notify(e.message)), [notify]);
  useEffect(() => { load(); }, [load]);

  const act = (p: Promise<unknown>) => p.then(load).catch((e) => notify(e.message));

  return (
    <div className="page">
      <h2>Members</h2>
      {canManage && (
        <form className="inline" onSubmit={(e) => { e.preventDefault(); act(api("/api/members/", "POST", { email, role: newRole }).then(() => setEmail(""))); }}>
          <input required type="email" placeholder="Email of a registered user" aria-label="Member email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select aria-label="New member role" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            {["ADMIN", "MEMBER", "VIEWER"].map((r) => <option key={r}>{r}</option>)}
          </select>
          <button>Add member</button>
        </form>
      )}
      <table>
        <thead><tr><th>Name</th><th>Email</th><th>Role</th>{canManage && <th />}</tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>{m.user.name}</td>
              <td>{m.user.email}</td>
              <td>
                {canManage ? (
                  <select aria-label={`Role of ${m.user.name}`} value={m.role} onChange={(e) => act(api(`/api/members/${m.id}/`, "PATCH", { role: e.target.value }))}>
                    {["OWNER", "ADMIN", "MEMBER", "VIEWER"].map((r) => <option key={r}>{r}</option>)}
                  </select>
                ) : m.role}
              </td>
              {canManage && <td><button className="ghost" onClick={() => act(api(`/api/members/${m.id}/`, "DELETE"))}>Remove</button></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Audit({ notify }: { notify: (m: string) => void }) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { api("/api/audit-log/").then((r) => setRows(r.results)).catch((e) => notify(e.message)); }, [notify]);
  return (
    <div className="page">
      <h2>Audit log</h2>
      <table>
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Entity</th></tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString()}</td>
              <td>{e.actor?.name ?? "System"}</td>
              <td className="mono">{e.action}</td>
              <td className="mono">{e.entity_type} #{e.entity_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [signedIn, setSignedIn] = useState(hasSession());
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [org, setOrg] = useState<Org | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [view, setView] = useState<View | null>(null);
  const [toast, setToast] = useState("");
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ key: "", name: "" });

  const notify = useCallback((m: string) => {
    setToast(m);
    window.setTimeout(() => setToast((t) => (t === m ? "" : t)), 5000);
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    api("/api/me/").then((me) => {
      setOrgs(me.organisations);
      setOrg(me.organisations[0] ?? null);
    }).catch((e) => notify(e.message));
  }, [signedIn, notify]);

  useEffect(() => {
    if (!org) return;
    setTenant(org.id);
    setView(null);
    api("/api/projects/").then((r) => {
      setProjects(r.results);
      if (r.results[0]) setView({ kind: "board", project: r.results[0].id });
    }).catch((e) => notify(e.message));
  }, [org, notify]);

  async function createProject(e: FormEvent) {
    e.preventDefault();
    try {
      const p = await api<Project>("/api/projects/", "POST", draft);
      setProjects((ps) => [...ps, p].sort((a, b) => a.key.localeCompare(b.key)));
      setView({ kind: "board", project: p.id });
      setCreating(false);
      setDraft({ key: "", name: "" });
    } catch (err) { notify((err as Error).message); }
  }

  if (!signedIn) return <AuthScreen onDone={() => setSignedIn(true)} />;

  return (
    <div className="shell">
      <nav className="side">
        <div className="brand">Projectboard</div>
        <label className="org">Organisation
          <select value={org?.id ?? ""} onChange={(e) => setOrg(orgs.find((o) => o.id === Number(e.target.value)) ?? null)}>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </label>
        {org && <div className="role">Your role: {org.role.toLowerCase()}</div>}
        <h4>Projects</h4>
        {projects.map((p) => (
          <button key={p.id} className={`nav${view?.kind === "board" && view.project === p.id ? " on" : ""}`} onClick={() => setView({ kind: "board", project: p.id })}>
            <span className="mono">{p.key}</span> {p.name}
          </button>
        ))}
        {org && isAdmin(org.role) && (creating ? (
          <form onSubmit={createProject} className="new-project">
            <input required maxLength={10} placeholder="Key" aria-label="Project key" value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
            <input required placeholder="Name" aria-label="Project name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <button>Create</button>
          </form>
        ) : <button className="nav add-project" onClick={() => setCreating(true)}>New project</button>)}
        <h4>Organisation</h4>
        <button className={`nav${view?.kind === "members" ? " on" : ""}`} onClick={() => setView({ kind: "members" })}>Members</button>
        {org && isAdmin(org.role) && <button className={`nav${view?.kind === "audit" ? " on" : ""}`} onClick={() => setView({ kind: "audit" })}>Audit log</button>}
        <button className="nav signout" onClick={() => { logout(); setSignedIn(false); setOrg(null); setOrgs([]); setProjects([]); setView(null); }}>Sign out</button>
      </nav>
      <main className="main">
        {view?.kind === "board" && org && <Board key={view.project} projectId={view.project} role={org.role} notify={notify} />}
        {view?.kind === "members" && org && <Members role={org.role} notify={notify} />}
        {view?.kind === "audit" && <Audit notify={notify} />}
        {!view && <p className="muted pad">{org && isAdmin(org.role) ? "Create a project to get started." : "No project to show yet."}</p>}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
