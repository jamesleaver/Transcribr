import { useApp } from "../state/store";

// P0: read-only list from /api/state. Open-for-review, reveal and the
// native "Open transcript…" picker arrive in Phases 3–4.

export default function LibraryView() {
  const recents = useApp((s) => s.snapshot?.recents ?? []);

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Library</h1>
        <p className="mt-1 text-sm text-muted">Recent transcripts on this Mac.</p>
      </header>

      {recents.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge p-10 text-center text-sm text-muted">
          Nothing here yet — transcripts you create or open will appear here.
        </div>
      ) : (
        <ul className="divide-y divide-edge overflow-hidden rounded-xl border border-edge bg-surface">
          {recents.map((r) => (
            <li key={r.path} className="flex items-baseline gap-3 px-4 py-3">
              <span className="text-sm font-medium">{r.name}</span>
              <span className="truncate text-xs text-muted">{r.path}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
