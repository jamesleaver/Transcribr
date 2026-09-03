import { useEffect, useRef, useState } from "react";
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

interface SearchHit {
  index: number;
  start: number;
  text: string;
  speaker: string | null;
}
interface SearchFile {
  path: string;
  name: string;
  folder: string;
  title: string | null;
  hits: SearchHit[];
}
interface SearchResponse {
  results: SearchFile[];
  searched: number;
  unreadable: number;
  query: string;
}

function stamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function SearchPanel() {
  const [query, setQuery] = useState("");
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = useRef(0);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setRes(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    // Each keystroke would otherwise re-read every transcript on disk.
    const token = ++latest.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const found = await api.get<SearchResponse>(
            `/api/transcripts/search?q=${encodeURIComponent(q)}`,
          );
          if (token === latest.current) setRes(found);
        } catch {
          if (token === latest.current) setRes(null);
        } finally {
          if (token === latest.current) setBusy(false);
        }
      })();
    }, 350);
    return () => clearTimeout(timer);
  }, [query]);

  const groups: [string, SearchFile[]][] = [];
  for (const file of res?.results ?? []) {
    const last = groups[groups.length - 1];
    if (last && last[0] === file.folder) last[1].push(file);
    else groups.push([file.folder, [file]]);
  }

  return (
    <section className="mb-8">
      <input
        className="w-full rounded-lg border border-edge bg-surface px-3 py-2 text-sm"
        placeholder="Search inside every transcript — a phrase, a name, a place"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {query.trim().length >= 2 && (
        <div className="mt-3">
          {busy ? (
            <p className="text-sm text-muted">Reading transcripts…</p>
          ) : !res || res.results.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing found in {res?.searched ?? 0} transcripts.
            </p>
          ) : (
            <>
              <p className="mb-3 text-xs text-muted">
                {res.results.length} of {res.searched} transcripts mention
                “{res.query}”.
              </p>
              <div className="flex flex-col gap-5">
                {groups.map(([folder, files]) => (
                  <div key={folder}>
                    <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                      {folder}
                    </h2>
                    <div className="flex flex-col gap-3">
                      {files.map((f) => (
                        <div
                          key={f.path}
                          className="rounded-xl border border-edge bg-surface p-3"
                        >
                          <button
                            className="mb-1.5 block text-left text-sm font-medium hover:underline"
                            onClick={() => void openForReview(f.path)}
                            title={f.path}
                          >
                            {f.title || f.name}
                          </button>
                          <ul className="flex flex-col gap-1">
                            {f.hits.map((h) => (
                              <li
                                key={h.index}
                                className="flex gap-2 text-xs leading-relaxed"
                              >
                                <span className="shrink-0 font-mono text-muted">
                                  [{stamp(h.start)}]
                                </span>
                                <span>
                                  {h.speaker ? (
                                    <span className="font-medium">
                                      {h.speaker}:{" "}
                                    </span>
                                  ) : null}
                                  {h.text}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
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

      <SearchPanel />

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
                <span className="flex items-center gap-2">
                  <span className="truncate">{r.name}</span>
                  {r.verified ? (
                    <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
                      Verified
                    </span>
                  ) : r.reviewed ? (
                    <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent">
                      Reviewed
                    </span>
                  ) : null}
                </span>
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
