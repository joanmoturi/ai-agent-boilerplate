export function ErrorMessage({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-lg border border-red-800 bg-red-950 p-3 text-sm text-red-200">{message}</p>;
}
