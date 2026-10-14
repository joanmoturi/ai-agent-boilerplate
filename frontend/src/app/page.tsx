"use client";

import { useState } from "react";
import { AgentWorkspace } from "@/features/agent/agent-workspace";
import { AuthForm } from "@/features/auth/auth-form";
import { useAuth } from "@/features/auth/auth-provider";
import { ErrorMessage } from "@/components/error-message";

export default function Home() {
  const { user, loading, error, logout } = useAuth();
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  async function signOut() {
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
    } catch (cause: unknown) {
      setLogoutError(cause instanceof Error ? cause.message : "Sign out failed.");
    } finally {
      setLoggingOut(false);
    }
  }

  return <main className="mx-auto max-w-3xl space-y-6 px-4 py-12">
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div><h1 className="text-3xl font-bold">AI Fullstack Starter</h1><p className="mt-2 text-slate-400">Typed contracts. Stateful agents. Modular architecture.</p></div>
      {user && <div className="space-y-2"><p className="text-sm">{user.email}</p><button className="button" disabled={loggingOut} onClick={signOut}>Sign out</button></div>}
    </header>
    <ErrorMessage message={logoutError ?? error} />
    {loading ? <p role="status">Restoring session...</p> : user ? <AgentWorkspace key={user.id} /> : <AuthForm />}
  </main>;
}
