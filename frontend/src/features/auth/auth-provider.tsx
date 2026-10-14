"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api-client";
import { authSchema, type User } from "@/lib/contracts";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  error: string | null;
  authenticate: (mode: "login" | "register", email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const unsubscribe = api.onSessionExpired(() => { if (active) setUser(null); });
    async function restore() {
      try {
        const session = await api.refresh();
        if (active) setUser(session.user);
      } catch (cause: unknown) {
        if (active && (!(cause instanceof ApiError) || cause.status !== 401)) {
          setError(cause instanceof Error ? cause.message : "Could not restore the session.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void restore();
    return () => { active = false; unsubscribe(); };
  }, []);

  async function authenticate(mode: "login" | "register", email: string, password: string) {
    const session = await api.request(
      `/auth/${mode}`, authSchema,
      { method: "POST", body: JSON.stringify({ email, password }) }, false
    );
    api.setSession(session);
    setUser(session.user);
    setError(null);
  }

  async function logout() {
    await api.logout();
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, error, authenticate, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider.");
  return context;
}
