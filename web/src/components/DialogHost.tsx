import { useEffect } from "react";
import { useDialogs } from "../state/dialogs";

// Renders the queued promise-based dialogs (the messagebox
// replacement). Esc answers no/dismiss; Enter gives the dialog's
// default answer (Tk's default="no" overwrite semantics preserved).

export default function DialogHost() {
  const current = useDialogs((s) => s.current);
  const answer = useDialogs((s) => s.answer);

  useEffect(() => {
    if (!current) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        answer(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        answer(current.spec.kind === "confirm" ? (current.spec.defaultAnswer ?? true) : true);
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [current, answer]);

  if (!current) return null;
  const { spec } = current;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6">
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-md rounded-xl border border-edge bg-surface p-5 shadow-2xl"
      >
        <h2
          className={`mb-2 text-base font-semibold ${spec.kind === "error" ? "text-red-500" : ""}`}
        >
          {spec.title}
        </h2>
        <p className="whitespace-pre-wrap text-sm text-fg">{spec.body}</p>
        {spec.detail && (
          <pre className="mt-3 max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-surface-2 p-3 text-xs text-muted">
            {spec.detail}
          </pre>
        )}
        <div className="mt-5 flex justify-end gap-2">
          {spec.kind === "confirm" && (
            <button
              className="rounded-lg border border-edge px-4 py-2 text-sm font-medium hover:bg-surface-2"
              onClick={() => answer(false)}
            >
              {spec.cancelLabel ?? "Cancel"}
            </button>
          )}
          <button
            className={`rounded-lg px-4 py-2 text-sm font-medium text-accent-fg ${
              spec.kind === "error" ? "bg-red-500" : "bg-accent"
            }`}
            onClick={() => answer(true)}
          >
            {spec.kind === "confirm" ? (spec.confirmLabel ?? "OK") : "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}
