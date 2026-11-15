/** Only allow same-app relative paths as a post-login redirect target (no `//host` or absolute URLs). */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  if (next.startsWith("/login") || next.startsWith("/register")) return fallback;
  return next;
}
