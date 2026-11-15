// AuthContext: token + principal + company (persisted via session.ts), login/register/logout.
import { createContext, useCallback, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { endpoints } from "../api/endpoints";
import { clearIdempotencyKeys } from "../api/hooks";
import type { Company, LoginRequest, Principal, RegisterRequest } from "../api/types";
import { clearSession, getSession, setSession, subscribeSession, type Session } from "./session";

type AuthValue = {
  session: Session | null;
  principal: Principal | null;
  company: Company | null;
  isAuthenticated: boolean;
  isUser: boolean;
  /** Throws ApiError on failure (render it inline on the login form). */
  login: (b: LoginRequest) => Promise<Session>;
  register: (b: RegisterRequest) => Promise<Session>;
  logout: () => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const session = useSyncExternalStore(subscribeSession, getSession, getSession);

  const login = useCallback(
    async (b: LoginRequest) => {
      const r = await endpoints.login(b);
      // Company name comes from /api/me (login returns only the user).
      let company: Company | null = null;
      try {
        const me = await endpoints.me({ token: r.token });
        company = me.company;
      } catch {
        company = null;
      }
      const s: Session = { token: r.token, principal: r.user, company };
      qc.clear();
      clearIdempotencyKeys();
      setSession(s);
      return s;
    },
    [qc],
  );

  const register = useCallback(
    async (b: RegisterRequest) => {
      const r = await endpoints.register(b);
      const s: Session = { token: r.token, principal: r.user, company: r.company };
      qc.clear();
      clearIdempotencyKeys();
      setSession(s);
      return s;
    },
    [qc],
  );

  const logout = useCallback(() => {
    clearSession();
    qc.clear();
    clearIdempotencyKeys();
  }, [qc]);

  const value = useMemo<AuthValue>(
    () => ({
      session,
      principal: session?.principal ?? null,
      company: session?.company ?? null,
      isAuthenticated: !!session,
      isUser: session?.principal.kind === "user",
      login,
      register,
      logout,
    }),
    [session, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth must be used inside <AuthProvider>");
  return v;
}
