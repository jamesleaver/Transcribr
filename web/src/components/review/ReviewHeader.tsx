import { useReview } from "../../state/reviewStore";

export default function ReviewHeader() {
  const doc = useReview((s) => s.doc);
  if (!doc) return null;
  const name = doc.out_path.split("/").pop() ?? doc.out_path;

  const saveThen = (mode: "labels" | "no_labels" | "revision") => async () => {
    await useReview.getState().commitPendingEdit();
    await useReview.getState().save(mode);
  };

  const btn =
    "rounded-lg border border-edge px-3 py-2 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";
  const accentBtn =
    "rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-accent-fg disabled:opacity-40";

  return (
    <header className="flex items-center gap-3 border-b border-edge px-6 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold" title={doc.out_path}>
          {name}
        </h1>
        <div className="text-xs text-muted">
          {doc.labelled} of {doc.total} paragraphs labelled
          {doc.diarized && (
            <span title="Labels were pre-filled by voice detection. Press N to jump to anything it left uncertain.">
              {" "}· speakers suggested automatically — please verify
            </span>
          )}
        </div>
      </div>

      <div className="flex gap-1">
        <button className={btn} disabled={!doc.can_undo}
          title="Undo (⌘Z)"
          onClick={() => void useReview.getState().undo()}>
          ↺ Undo
        </button>
        <button className={btn} disabled={!doc.can_redo}
          title="Redo (⌘⇧Z)"
          onClick={() => void useReview.getState().redo()}>
          ↻ Redo
        </button>
      </div>

      <div className="flex gap-2">
        <button className={btn} title="Write a PDF copy alongside the transcript — the review stays open"
          onClick={() => void useReview.getState().exportAs("pdf")}>
          Export PDF
        </button>
        {doc.loaded ? (
          <>
            <button className={accentBtn} onClick={() => void saveThen("labels")()}>
              Save (overwrite)
            </button>
            <button className={btn} onClick={() => void saveThen("revision")()}>
              Save as revision
            </button>
            <button className={btn}
              onClick={() => void useReview.getState().closeWithoutSaving()}>
              Close without saving
            </button>
          </>
        ) : (
          <>
            <button className={accentBtn} onClick={() => void saveThen("labels")()}>
              Save with labels
            </button>
            <button className={btn} onClick={() => void saveThen("no_labels")()}>
              Save without labels
            </button>
          </>
        )}
      </div>
    </header>
  );
}
