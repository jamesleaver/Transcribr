import { useEffect, useRef } from "react";
import { api } from "../api/client";
import { useApp } from "../state/store";
import { useRun } from "../state/runStore";

// Sticky bottom run bar: Run/Stop, progress, status line, log drawer
// toggle, Open/Reveal after completion. The web twin of the Tk app's
// run controls + progress card.

function LogDrawer() {
  const log = useRun((s) => s.log);
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);
  return (
    <pre
      ref={ref}
      className="mt-3 h-44 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed text-muted"
    >
      {log || "No output yet."}
    </pre>
  );
}

export default function RunBar() {
  const phase = useRun((s) => s.phase);
  const staged = useRun((s) => s.staged);
  const currentFile = useRun((s) => s.currentFile);
  const progress = useRun((s) => s.progress);
  const outPath = useRun((s) => s.outPath);
  const revealLabel = useApp((s) => s.meta?.reveal_label ?? "Reveal");
  const showDetails = useApp((s) => s.settings?.show_details ?? false);

  const running = phase === "running" || phase === "stopping";
  const runLabel = running
    ? phase === "stopping"
      ? "Stopping…"
      : "Running…"
    : staged.length > 1
      ? `Run batch (${staged.length} files)`
      : "Run Transcription";

  const pct = progress?.pct ?? (phase === "done" ? 100 : 0);
  const statusText =
    progress?.status_text ??
    (phase === "running" ? "Starting…" : phase === "done" ? "Done"
      : phase === "error" ? "Failed" : phase === "cancelled" ? "Stopped" : "");

  return (
    <div className="sticky bottom-0 border-t border-edge bg-surface/95 px-6 py-4 backdrop-blur">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3">
          <button
            className="min-w-[170px] rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-accent-fg disabled:opacity-40"
            disabled={running || staged.length === 0}
            onClick={() => void useRun.getState().run()}
          >
            {runLabel}
          </button>
          <button
            className="rounded-lg border border-edge px-4 py-2.5 text-sm font-medium hover:bg-surface-2 disabled:opacity-40"
            disabled={!running || phase === "stopping"}
            onClick={() => void useRun.getState().stop()}
          >
            Stop
          </button>

          <div className="min-w-0 flex-1">
            {(running || phase === "done" || phase === "error" || phase === "cancelled") && (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-xs font-medium">{currentFile ?? ""}</span>
                  <span className="shrink-0 text-xs tabular-nums text-muted">
                    {running || phase === "done" ? `${Math.round(pct)}%` : ""}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
                  <div
                    className="h-full rounded-full bg-accent transition-[width] duration-300"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="mt-1 truncate text-xs text-muted">{statusText}</div>
              </>
            )}
          </div>

          {phase === "done" && outPath && (
            <div className="flex shrink-0 gap-2">
              <button
                className="rounded-lg border border-edge px-3 py-2 text-xs font-medium hover:bg-surface-2"
                onClick={() => void api.post("/api/path/open", { path: outPath })}
              >
                Open Output
              </button>
              <button
                className="rounded-lg border border-edge px-3 py-2 text-xs font-medium hover:bg-surface-2"
                onClick={() => void api.post("/api/path/reveal", { path: outPath })}
              >
                {revealLabel}
              </button>
            </div>
          )}

          <button
            className="shrink-0 text-xs text-muted hover:text-fg"
            onClick={() => useApp.getState().updateSettings({ show_details: !showDetails })}
          >
            {showDetails ? "Hide details ▾" : "Show details ▸"}
          </button>
        </div>

        {showDetails && <LogDrawer />}
      </div>
    </div>
  );
}
