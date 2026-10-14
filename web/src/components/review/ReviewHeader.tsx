import { useEffect, useRef, useState } from "react";
import { useReview } from "../../state/reviewStore";

type SaveMode = "labels" | "no_labels" | "revision";

/** A small menu that shuts on a click elsewhere or Escape. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", away, true);
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("mousedown", away, true);
      document.removeEventListener("keydown", key, true);
    };
  }, [open, close]);
  return ref;
}

const menuItem =
  "block w-full px-3 py-1.5 text-left text-xs hover:bg-surface-2 disabled:opacity-40 disabled:hover:bg-transparent";

/** One Save button; the other ways to save, export or leave sit in its
 *  menu. What Save does follows the document: overwrite a reopened
 *  file, save a new one with its labels. */
function SaveButton({ saveThen }: { saveThen: (m: SaveMode) => () => Promise<void> }) {
  const doc = useReview((s) => s.doc)!;
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const ref = useDismiss(open, close);
  const qa = doc.layout === "qa";
  const pick = (fn: () => unknown) => () => {
    close();
    void fn();
  };
  const liveNote = doc.live ? "Available once the transcription has finished" : undefined;
  return (
    <div ref={ref} className="relative flex">
      <button
        className="rounded-l-lg bg-accent px-3 py-2 text-xs font-semibold text-accent-fg disabled:opacity-40"
        disabled={doc.live}
        title={liveNote ?? (doc.loaded ? "Save over the file you opened" : "Save the transcript")}
        onClick={() => void saveThen("labels")()}
      >
        Save
      </button>
      <button
        className="rounded-r-lg border-l border-white/30 bg-accent px-2 py-2 text-xs font-semibold text-accent-fg"
        aria-label="More ways to save"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ▾
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-20 mt-1 min-w-52 rounded-lg border border-edge bg-surface py-1 shadow-lg">
          {doc.loaded && (
            <button className={menuItem} onClick={pick(saveThen("revision"))}>
              Save as a new revision
            </button>
          )}
          {!doc.loaded && !qa && (
            <button className={menuItem} disabled={doc.live} onClick={pick(saveThen("no_labels"))}>
              Save without speaker labels
            </button>
          )}
          <button className={menuItem} disabled={doc.live} title={liveNote}
            onClick={pick(() => useReview.getState().exportAs("pdf"))}>
            Export a PDF copy
          </button>
          {doc.loaded && (
            <>
              <div className="my-1 border-t border-edge" />
              <button className={menuItem}
                onClick={pick(() => useReview.getState().closeWithoutSaving())}>
                Close without saving
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const SHORTCUTS: [string, string][] = [
  ["1–9 / 0", "Label the paragraph with a speaker / clear it"],
  ["↑ ↓", "Previous / next paragraph"],
  ["Enter", "Edit the paragraph"],
  ["M", "Join to the paragraph above"],
  ["Double-click", "Split at that word"],
  ["Right-click", "Re-transcribe, edit, join or delete"],
  ["N", "Next paragraph needing attention"],
  ["U / ⇧U", "Next / previous unsure passage ([indistinct])"],
  ["P / ⌘P", "Play the paragraph / play on from it"],
  ["⌘Z / ⌘⇧Z", "Undo / redo"],
  ["⌘F", "Find"],
];

const EDIT_SHORTCUTS: [string, string][] = [
  ["Enter", "Finish editing"],
  ["Esc", "Cancel"],
  ["Ctrl+Enter", "Split at the cursor"],
  ["Ctrl+M", "Join to the paragraph above"],
  ["Ctrl+↑ ↓", "Edit the previous / next paragraph"],
];

/** "?" in the header, and the ? key: the keyboard shortcuts. */
function ShortcutsButton() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const ref = useDismiss(open, close);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (e.key === "?" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const table = (rows: [string, string][]) => (
    <table className="w-full text-xs">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="whitespace-nowrap py-0.5 pr-4 font-mono text-[11px] font-semibold">{k}</td>
            <td className="py-0.5 text-muted">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div ref={ref} className="relative">
      <button
        className="flex h-8 w-8 items-center justify-center rounded-lg border border-edge text-sm hover:bg-surface-2"
        title="Keyboard shortcuts (?)"
        aria-label="Keyboard shortcuts"
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-lg border border-edge bg-surface p-3 shadow-lg">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Reviewing</div>
          {table(SHORTCUTS)}
          <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">While editing</div>
          {table(EDIT_SHORTCUTS)}
        </div>
      )}
    </div>
  );
}

export default function ReviewHeader({
  onToggleTools,
  toolsOpen,
}: {
  onToggleTools?: () => void;
  toolsOpen?: boolean;
}) {
  const doc = useReview((s) => s.doc);
  if (!doc) return null;
  const name = doc.out_path.split("/").pop() ?? doc.out_path;

  const saveThen = (mode: SaveMode) => async () => {
    await useReview.getState().commitPendingEdit();
    await useReview.getState().save(mode);
  };

  const btn =
    "rounded-lg border border-edge px-3 py-2 text-xs font-medium hover:bg-surface-2 disabled:opacity-40";
  const iconBtn =
    "flex h-8 w-8 items-center justify-center rounded-lg border border-edge text-sm hover:bg-surface-2 disabled:opacity-40";

  return (
    <header className="flex items-center gap-3 border-b border-edge px-6 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold" title={doc.out_path}>
          {name}
        </h1>
        <div className="text-xs text-muted">
          {doc.live ? (
            <span title="The engine is still transcribing. You can correct anything above the greyed-out tail.">
              transcribing — {doc.sealed_count} of {doc.total} paragraphs
              settled
            </span>
          ) : (
            <>{doc.labelled} of {doc.total} paragraphs labelled</>
          )}
          {doc.diarized && (
            <span title="Labels were pre-filled by voice detection. Press N to jump to anything it left uncertain.">
              {" "}· speakers suggested automatically — please verify
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1">
        <button className={iconBtn} disabled={!doc.can_undo}
          title="Undo (⌘Z)" aria-label="Undo"
          onClick={() => void useReview.getState().undo()}>
          ↺
        </button>
        <button className={iconBtn} disabled={!doc.can_redo}
          title="Redo (⌘⇧Z)" aria-label="Redo"
          onClick={() => void useReview.getState().redo()}>
          ↻
        </button>
        <ShortcutsButton />
      </div>

      <div className="flex items-center gap-2">
        {onToggleTools && (
          <button className={btn} onClick={onToggleTools}>
            {toolsOpen ? "Hide tools" : "Tools"}
          </button>
        )}
        <SaveButton saveThen={saveThen} />
      </div>
    </header>
  );
}
