// Session store shared by the API client and AuthContext (token + principal in localStorage).
import type { Company, Principal } from "../api/types";

export type Session = { token: string; principal: Principal; company: Company | null };

const KEY = "aimoannos.session";
type Listener = () => void;
const listeners = new Set<Listener>();
let cache: Session | null | undefined;

function read(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s && typeof s.token === "string" ? s : null;
  } catch {
    return null;
  }
}

export function getSession(): Session | null {
  if (cache === undefined) cache = read();
  return cache;
}

export function getToken(): string | null {
  return getSession()?.token ?? null;
}

export function setSession(session: Session): void {
  cache = session;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* storage unavailable: keep in memory */
  }
  listeners.forEach((l) => l());
}

export function clearSession(): void {
  cache = null;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
