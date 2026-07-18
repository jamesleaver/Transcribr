import { api, ApiError } from "../api/client";
import { errorDialog } from "../state/dialogs";
import { useApp } from "../state/store";
import { useReview, type ReviewPayload } from "../state/reviewStore";

// Recent transcripts + "Open transcript…". Opening enters review mode
// with the parsed content (speakers mapped to slots server-side).

async function openForReview(path: string): Promise<void> {
  try {
    const res = await api.post<{ review: ReviewPayload }>(
      "/api/transcripts/open",
      { path },
    );
    useReview.getState().openDoc(res.review);
    useApp.getState().setView("review");
  } catch (err) {
    if (err instanceof ApiError) {
      const title =
        err.code === "too_many_speakers" ? "Too many speakers"
        : err.code === "parse_error" ? "Cannot open transcript"
        : err.code === "review_open" ? "A review is already open"
        : "Cannot open";
    void errorDialog(title, err.message);
    } else {
      throw err;
    }
  }
}

async function pickAndOpen(): Promise<void> {
  try {
    const res = await api.post<{ path?: string; cancelled?: boolean }>(
      "/api/pick",
      { kind: "transcript" },
    );
    if (res.path) await openForReview(res.path);
  } catch (err) {
    if (err instanceof ApiError && err.code === "no_dialog") {
      void errorDialog("No file dialog here", err.message);
    } else {
      throw err;
    }
  }
}

export default function LibraryView() {
  const recents = useApp((s) => s.snapshot?.recents ?? []);
  const revealLabel = useApp((s) => s.meta?.reveal_label ?? "Reveal");
  const btn =
    "rounded-lg border border-edge px-2.5 py-1 text-xs font-medium hover:bg-surface-2";

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Recent transcripts</h1>
          <p className="mt-1 text-sm text-muted">
            Recent transcripts — open one to review and label speakers.
          </p>
        </div>
        <button
          className="rounded-lg bg-accent px-3.5 py-2 text-xs font-semibold text-accent-fg"
          onClick={() => void pickAndOpen()}
        >
          Open transcript…
        </button>
      </header>

      {recents.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge p-10 text-center text-sm text-muted">
          Nothing here yet — transcripts you create or open will appear here.
        </div>
      ) : (
        <ul className="divide-y divide-edge overflow-hidden rounded-xl border border-edge bg-surface">
          {recents.map((r) => (
            <li key={r.path} className="flex items-center gap-3 px-4 py-3">
              <button
                className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:text-accent"
                title={`Open for review: ${r.path}`}
                onClick={() => void openForReview(r.path)}
              >
                {r.name}
                <span className="mt-0.5 block truncate text-xs font-normal text-muted">
                  {r.path}
                </span>
              </button>
              <button className={btn} onClick={() => void openForReview(r.path)}>
                Review
              </button>
              <button
                className={btn}
                onClick={() => void api.post("/api/path/reveal", { path: r.path })}
              >
                {revealLabel}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
