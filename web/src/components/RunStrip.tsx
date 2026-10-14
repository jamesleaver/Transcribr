import { api } from "../api/client";
import { useRun } from "../state/runStore";

// The whole of the run controls while a transcription is going: one
// thin line above the document. Everything else about the run - the
// file list, the engine options, the log - belongs to the setup
// surface, which is not what somebody reading a transcript needs in
// front of them.

export default function RunStrip() {
  const phase = useRun((s) => s.phase);
  const currentFile = useRun((s) => s.currentFile);
  const progress = useRun((s) => s.progress);
  const batch = useRun((s) => s.batch);

  const running = phase === "running" || phase === "stopping";
  // The repair pass runs after the transcription has finished and the
  // run has gone idle, but it does rewrite the transcript under the
  // reader — so it keeps the strip up, minus the Stop button, rather
  // than changing the document with nothing on screen to explain it.
  const repairing = !running && progress?.stage === "retranscribing";
  if (!running && !repairing) return null;

  const name = currentFile ? (currentFile.split("/").pop() ?? "") : "";
  const pct = Math.max(0, Math.min(100, progress?.pct ?? 0));
  const indeterminate = progress?.indeterminate ?? false;
  const stage = progress?.stage;
  const caption =
    stage === "downloading"
      ? "Downloading model (first use)"
      : stage === "loading"
        ? "Loading model"
        : stage === "retranscribing"
          ? "Re-reading flagged sections"
          : stage === "diarizing"
            ? "Listening for voices"
            : (progress?.status_text ?? "Starting…");

  return (
    <div className="flex items-center gap-3 border-b border-edge bg-surface px-6 py-2 text-xs">
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ background: "var(--accent)" }}
        aria-hidden
      />
      <span className="max-w-[18ch] truncate font-medium" title={currentFile ?? ""}>
        {name}
      </span>
      {batch && batch.total > 1 && (
        <span className="shrink-0 text-muted">
          {batch.index + 1} of {batch.total}
        </span>
      )}

      <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2">
        {indeterminate ? (
          <div
            className="absolute inset-y-0 w-1/4 rounded-full animate-progress-indeterminate"
            style={{ background: "var(--accent)" }}
          />
        ) : (
          <div
            className="h-full rounded-full transition-[width] duration-300"
            style={{ width: `${pct}%`, background: "var(--accent)" }}
          />
        )}
      </div>

      <span className="shrink-0 truncate text-muted" title={caption}>
        {caption}
      </span>
      {!repairing && (
        <button
          className="shrink-0 rounded-md border border-edge px-2.5 py-1 font-medium hover:bg-surface-2 disabled:opacity-40"
          disabled={phase === "stopping"}
          title="Stop transcribing. Whatever has been transcribed so far is kept."
          onClick={() => void api.post("/api/run/stop")}
        >
          {phase === "stopping" ? "Stopping…" : "Stop"}
        </button>
      )}
    </div>
  );
}
