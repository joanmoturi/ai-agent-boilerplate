"use client";

export default function ErrorBoundary({ error, reset }: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <section role="alert" className="panel mx-auto mt-12 max-w-md space-y-4">
    <h1 className="text-xl font-semibold">The page could not be displayed</h1>
    <p>Please retry. If the problem persists, contact your application operator.</p>
    {error.digest && <p className="font-mono text-xs text-slate-400">Reference: {error.digest}</p>}
    <button className="button" onClick={reset}>Retry</button>
  </section>;
}
