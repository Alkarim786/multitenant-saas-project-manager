/** REST client: attaches the token and tenant header, and refreshes an expired token once. */

export class ApiError extends Error {
  constructor(public status: number, public body: any) {
    super(typeof body?.detail === "string" ? body.detail : firstError(body) ?? `Request failed (${status})`);
  }
}

function firstError(body: any): string | undefined {
  if (!body || typeof body !== "object") return undefined;
  const first = Object.values(body)[0];
  return Array.isArray(first) ? String(first[0]) : typeof first === "string" ? first : undefined;
}

const store = {
  get access() { return localStorage.getItem("access"); },
  get refresh() { return localStorage.getItem("refresh"); },
};

let tenantId: number | null = null;
export const setTenant = (id: number | null) => { tenantId = id; };
export const accessToken = () => store.access;
export const currentTenant = () => tenantId;
export const hasSession = () => !!store.access;

export function saveTokens(access: string, refresh?: string) {
  localStorage.setItem("access", access);
  if (refresh) localStorage.setItem("refresh", refresh);
}

export function logout() {
  localStorage.removeItem("access");
  localStorage.removeItem("refresh");
  tenantId = null;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (store.access) headers.Authorization = `Bearer ${store.access}`;
  if (tenantId !== null) headers["X-Tenant-ID"] = String(tenantId);
  return fetch(path, { ...init, headers });
}

async function refreshToken(): Promise<boolean> {
  if (!store.refresh) return false;
  const r = await fetch("/api/auth/token/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: store.refresh }),
  });
  if (!r.ok) return false;
  saveTokens((await r.json()).access);
  return true;
}

export async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const init: RequestInit = { method, body: body === undefined ? undefined : JSON.stringify(body) };
  let r = await send(path, init);
  if (r.status === 401 && (await refreshToken())) r = await send(path, init);
  if (r.status === 401 && store.access) {
    logout();
    location.reload();
  }
  const text = await r.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* a CSV download, not JSON */ }
  if (!r.ok) throw new ApiError(r.status, data);
  return data as T;
}
