"use client";

import { useState, type FormEvent } from "react";
import { api } from "@/lib/api-client";
import { runSchema, type AgentRun } from "@/lib/contracts";
import { ErrorMessage } from "@/components/error-message";

export function AgentWorkspace() {
  const [prompt, setPrompt] = useState("");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setRun(null);
    try {
      setRun(await api.request("/agent/runs", runSchema, {
        method: "POST", body: JSON.stringify({ prompt })
      }));
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Agent request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      setRun(await api.request(`/agent/runs/${run.id}`, runSchema));
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Could not reload the run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2 className="text-xl font-semibold">Agent workspace</h2>
      <p className="mt-2 text-slate-400">Ask a question, or try a calculation. The agent uses bounded, schema-validated steps.</p>
      <form onSubmit={submit} className="mt-4 space-y-4">
        <label className="block" htmlFor="prompt">Prompt</label>
        <textarea id="prompt" className="input min-h-32" required maxLength={4000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="What is 127 multiplied by 83?" />
        <button className="button" disabled={busy || !prompt.trim()}>{busy ? "Running..." : "Run agent"}</button>
      </form>
      <div className="mt-4"><ErrorMessage message={error ?? run?.error ?? null} /></div>
      {run && <article className="mt-6 space-y-3" aria-live="polite">
        <p className="text-sm text-slate-400">Status: {run.status} | Steps: {run.steps}</p>
        {run.answer && <p className="whitespace-pre-wrap break-words">{run.answer}</p>}
        <p className="break-all font-mono text-xs text-slate-500">Run: {run.id}</p>
        <button className="text-sm text-sky-300 underline" disabled={busy} onClick={reload}>Reload persisted run</button>
      </article>}
    </section>
  );
}
