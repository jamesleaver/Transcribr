import { useEffect, useState } from "react";
import { alertDialog } from "../state/dialogs";
import { useRun } from "../state/runStore";
import { useApp } from "../state/store";
import { inputCls } from "./fields";

// The Source column: drop zone, staged file(s), prompt field.
// Drops anywhere on the window are accepted (parity with the Tk app,
// which registers the whole root as a drop target). In browser dev
// mode the OS gives us no real paths, so we point at Browse instead;
// in the desktop app (P5) Python intercepts drops and pushes real
// paths over SSE.

function useWindowDrop(onHover: (hovering: boolean) => void) {
  useEffect(() => {
    let depth = 0;
    const enter = (e: DragEvent) => {
      e.preventDefault();
      depth += 1;
      onHover(true);
    };
    const leave = () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) onHover(false);
    };
    const over = (e: DragEvent) => e.preventDefault();
    const drop = (e: DragEvent) => {
      e.preventDefault();
      depth = 0;
      onHover(false);
      if (useApp.getState().meta?.ui_mode !== "webview") {
        void alertDialog(
          "Drag-and-drop needs the desktop app",
          "In a browser, dropped files don't reveal their location on disk. Use Browse instead — the desktop app accepts drops normally.",
        );
      }
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [onHover]);
}

function DropZone() {
  const [hover, setHover] = useState(false);
  const browse = useRun((s) => s.browse);
  useWindowDrop(setHover);

  return (
    <button
      onClick={() => void browse()}
      className="w-full rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors"
      style={{
        borderColor: "var(--drop-border)",
        background: hover ? "var(--drop-hover)" : "var(--drop-bg)",
        color: "var(--drop-fg)",
      }}
    >
      <div className="text-sm font-medium">
        Drop audio or video files here
      </div>
      <div className="mt-1 text-xs opacity-80">
        or click to browse — several files become a batch
      </div>
    </button>
  );
}

function SingleFileCard() {
  const staged = useRun((s) => s.staged);
  const outputTouched = useRun((s) => s.outputTouched);
  const outputPath = useRun((s) => s.outputPath);
  const file = staged[0];

  return (
    <div className="rounded-xl border border-edge bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="truncate text-xs text-muted">{file.path}</div>
        </div>
        <button
          className="shrink-0 rounded-lg border border-edge px-2.5 py-1 text-xs hover:bg-surface-2"
          onClick={() => useRun.getState().clearStaged()}
        >
          Remove
        </button>
      </div>
      <div className="mt-4 flex items-end gap-2">
        <label className="min-w-0 flex-1">
          <span className="mb-1.5 block text-xs font-medium text-muted">
            Save transcript as
          </span>
          <input
            className={`${inputCls} w-full`}
            value={outputTouched ? outputPath : ""}
            placeholder={useRun.getState().derivedOutput()}
            onChange={(e) => useRun.getState().setOutputPath(e.target.value)}
          />
        </label>
        <button
          className="rounded-lg border border-edge px-3 py-2 text-sm hover:bg-surface-2"
          onClick={() => void useRun.getState().saveAs()}
        >
          Choose…
        </button>
      </div>
    </div>
  );
}

function BatchList() {
  const staged = useRun((s) => s.staged);
  return (
    <div className="overflow-hidden rounded-xl border border-edge bg-surface">
      <div className="flex items-center justify-between border-b border-edge bg-surface-2 px-4 py-2.5">
        <span className="text-xs font-semibold">
          Batch — {staged.length} files
        </span>
        <button
          className="text-xs text-muted hover:text-fg"
          onClick={() => useRun.getState().clearStaged()}
        >
          Clear all
        </button>
      </div>
      <p className="border-b border-edge px-4 py-2 text-xs text-muted">
        Batch runs save each transcript next to its source — review is
        skipped. Open them from the Library afterwards.
      </p>
      <ul className="max-h-52 divide-y divide-edge overflow-y-auto">
        {staged.map((f, i) => (
          <li key={f.path} className="flex items-center gap-3 px-4 py-2">
            <span className="min-w-0 flex-1 truncate text-sm">{f.name}</span>
            <span className="hidden max-w-[40%] truncate text-xs text-muted sm:block">
              {f.path}
            </span>
            <button
              className="text-muted hover:text-fg"
              title="Remove"
              onClick={() => useRun.getState().removeStaged(i)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PromptCard() {
  const title = useApp((s) => s.settings?.title ?? "");
  const prompt = useApp((s) => s.settings?.prompt ?? "");
  return (
    <div className="flex flex-col gap-4 rounded-xl border border-edge bg-surface p-4">
      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-muted">
          Document title — heading at the top of the transcript. Not sent to
          the engine. Left blank, the file name is used.
        </span>
        <textarea
          className={`${inputCls} min-h-[38px] w-full resize-y`}
          rows={1}
          value={title}
          onChange={(e) => useApp.getState().updateSettings({ title: e.target.value })}
        />
      </label>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-muted">
          Context / vocabulary hint (optional) — primes the engine with
          names, acronyms and place names it may not know.
        </span>
        <textarea
          className={`${inputCls} min-h-[38px] w-full resize-y`}
          rows={1}
          value={prompt}
          placeholder="e.g. Macklebum, Bloggs, Mount Druitt, AVO, ICAC, DVEC"
          onChange={(e) => useApp.getState().updateSettings({ prompt: e.target.value })}
        />
        <span className="mt-1.5 block text-xs text-amber-600 dark:text-amber-500">
          ⚠ Priming can backfire. A prompt may bleed into the transcript or
          trigger hallucinations, especially on unclear audio or long
          silences. Leave it blank unless you need help with specific
          terms, and keep it to keywords rather than sentences.
        </span>
      </label>
    </div>
  );
}

export default function SourcePanel() {
  const count = useRun((s) => s.staged.length);
  return (
    <div className="flex flex-col gap-4">
      <DropZone />
      {count === 1 && <SingleFileCard />}
      {count > 1 && <BatchList />}
      <PromptCard />
    </div>
  );
}
