"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "@/lib/api-client";
import { providersSchema } from "@/lib/contracts";
import { ErrorMessage } from "@/components/error-message";
import { useAuth } from "./auth-provider";

export function AuthForm() {
  const { authenticate } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<{ local: boolean; oidc: boolean } | null>(null);

  useEffect(() => {
    let active = true;
    void api.request("/auth/providers", providersSchema, {}, false)
      .then((result) => { if (active) setProviders(result); })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load authentication providers.");
      });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authenticate(mode, email, password);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel mx-auto max-w-md">
      <h2 className="text-xl font-semibold">{mode === "login" ? "Sign in" : "Create an account"}</h2>
      <ErrorMessage message={error} />
      {providers?.local && <form onSubmit={submit} className="mt-4 space-y-4">
        <label className="block">Email<input className="input mt-1" type="email" autoComplete="email" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="block">Password<input className="input mt-1" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 12 : 1} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button className="button w-full" disabled={busy}>{busy ? "Working..." : mode === "login" ? "Sign in" : "Register"}</button>
        <button type="button" disabled={busy} className="text-sm text-sky-300 underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
          {mode === "login" ? "Create an account instead" : "Already registered? Sign in"}
        </button>
      </form>}
      {providers?.oidc && <a className="button mt-4 block text-center" href="/api/v1/auth/oidc/login">Continue with your identity provider</a>}
      {!providers && !error && <p className="mt-4 text-slate-400">Loading providers...</p>}
    </section>
  );
}
