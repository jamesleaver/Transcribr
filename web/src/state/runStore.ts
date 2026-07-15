import { create } from "zustand";
import { api, ApiError } from "../api/client";
import type { BatchDone, InspectedFile, Progress, RunState } from "../api/types";
import { alertDialog, confirmDialog, errorDialog } from "./dialogs";
import { useApp } from "./store";

// State + actions for the Transcribe surface: the staged file list,
// the mirrored server run state, and the run/stop flows including
// every confirmation dialog the Tk app shows.

const NO_ENGINE_TITLE = "No transcription engine installed";

function swapExt(path: string, fmt: string): string {
  // Parity with _on_format_changed (5525): swap the extension only if
  // the current one is a known transcript format.
  const m = path.match(/^(.*)\.(txt|docx|pdf)$/i);
  return m ? `${m[1]}.${fmt}` : path;
}

interface RunSlice {
  staged: InspectedFile[];
  outputPath: string; // single-file mode; "" = derive server-side
  outputTouched: boolean;

  phase: RunState["phase"];
  runId: number;
  currentFile: string | null;
  batch: RunState["batch"];
  outPath: string | null;
  progress: Progress | null;
  log: string;

  addPaths: (paths: string[]) => Promise<void>;
  removeStaged: (index: number) => void;
  clearStaged: () => void;
  setOutputPath: (value: string) => void;
  onFormatChanged: (fmt: string) => void;
  derivedOutput: () => string;

  browse: () => Promise<void>;
  saveAs: () => Promise<void>;
  run: () => Promise<void>;
  stop: () => Promise<void>;

  applyRunState: (rs: RunState, opts?: { resync?: boolean }) => void;
  appendLog: (text: string) => void;
  applyProgress: (p: Progress) => void;
  onBatchDone: (b: BatchDone) => void;
}

export const useRun = create<RunSlice>((set, get) => ({
  staged: [],
  outputPath: "",
  outputTouched: false,

  phase: "idle",
  runId: 0,
  currentFile: null,
  batch: null,
  outPath: null,
  progress: null,
  log: "",

  addPaths: async (paths) => {
    if (!paths.length) return;
    const { files } = await api.post<{ files: InspectedFile[] }>(
      "/api/files/inspect",
      { paths },
    );
    set((s) => {
      const known = new Set(s.staged.map((f) => f.path));
      const merged = [...s.staged, ...files.filter((f) => !known.has(f.path))];
      return { staged: merged, outputTouched: false, outputPath: "" };
    });
  },

  removeStaged: (index) =>
    set((s) => ({ staged: s.staged.filter((_, i) => i !== index) })),

  clearStaged: () => set({ staged: [], outputPath: "", outputTouched: false }),

  setOutputPath: (value) => set({ outputPath: value, outputTouched: true }),

  onFormatChanged: (fmt) =>
    set((s) =>
      s.outputTouched && s.outputPath
        ? { outputPath: swapExt(s.outputPath, fmt) }
        : {},
    ),

  derivedOutput: () => {
    const s = get();
    if (s.outputTouched && s.outputPath) return s.outputPath;
    return s.staged[0]?.derived_output ?? "";
  },

  browse: async () => {
    try {
      const res = await api.post<{ paths?: string[]; path?: string; cancelled?: boolean }>(
        "/api/pick",
        { kind: "media", multiple: true },
      );
      if (res.cancelled) return;
      await get().addPaths(res.paths ?? (res.path ? [res.path] : []));
    } catch (err) {
      if (err instanceof ApiError && err.code === "no_dialog") {
        void alertDialog("No file dialog here", err.message);
      } else {
        throw err;
      }
    }
  },

  saveAs: async () => {
    const staged = get().staged;
    if (staged.length !== 1) return;
    const fmt = useApp.getState().settings?.output_format ?? "docx";
    const res = await api.post<{ path?: string; cancelled?: boolean }>("/api/pick", {
      kind: "save-output",
      initial: get().derivedOutput().split("/").pop(),
      format: fmt,
    });
    if (res.path) get().setOutputPath(res.path);
  },

  run: async () => {
    const s = get();
    const single = s.staged.length === 1;
    const doRun = async (force: boolean): Promise<void> => {
      if (single) {
        await api.post("/api/run", {
          input: s.staged[0].path,
          output: s.outputTouched ? s.outputPath : "",
          force,
        });
      } else {
        await api.post("/api/batch", {
          files: s.staged.map((f) => f.path),
          force,
        });
      }
    };

    if (s.staged.length === 0) {
      void errorDialog(
        "Missing input",
        "Please choose an input audio/video file, or add several for a batch run.",
      );
      return;
    }

    try {
      await doRun(false);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.code === "output_exists") {
        const ok = await confirmDialog({
          title: "Overwrite existing file?",
          body: "The output file already exists. Do you want to overwrite it?",
          detail: err.extra.path as string | undefined,
          confirmLabel: "Overwrite",
          defaultAnswer: false,
        });
        if (ok) await doRun(true).catch(showRunError);
      } else if (err.code === "outputs_exist") {
        const existing = (err.extra.existing as string[] | undefined) ?? [];
        const total = (err.extra.total as number | undefined) ?? existing.length;
        const more =
          total > existing.length ? `\n… and ${total - existing.length} more` : "";
        const ok = await confirmDialog({
          title: "Overwrite existing files?",
          body: `${total} output file(s) already exist and will be overwritten. Continue?`,
          detail: existing.join("\n") + more,
          confirmLabel: "Overwrite",
          defaultAnswer: false,
        });
        if (ok) await doRun(true).catch(showRunError);
      } else {
        showRunError(err);
      }
    }
  },

  stop: async () => {
    await api.post("/api/run/stop");
  },

  applyRunState: (rs, opts) => {
    set((s) => ({
      phase: rs.phase,
      runId: rs.run_id,
      currentFile: rs.file,
      batch: rs.batch,
      outPath: rs.out_path,
      progress: rs.progress ?? (rs.phase === "running" && rs.run_id !== s.runId ? null : s.progress),
      // A new run id means a fresh log; resync snapshots carry the tail.
      log: rs.log !== undefined ? rs.log : rs.run_id !== s.runId ? "" : s.log,
    }));
    if (rs.phase === "error" && rs.first_line && !opts?.resync) {
      void errorDialog("Error", rs.first_line, undefined);
    }
  },

  appendLog: (text) =>
    set((s) => ({
      log: (s.log + text).slice(-100_000),
    })),

  applyProgress: (p) => set({ progress: p }),

  onBatchDone: (b) => {
    void alertDialog(
      b.stopped ? "Batch stopped" : "Batch complete",
      `Transcribed ${b.succeeded.length} file(s).\nFailed: ${b.failed.length}.\n\n` +
        "Open each transcript from the Library to review and label speakers.",
      b.failed.map(([p, why]) => `${p.split("/").pop()}: ${why}`).join("\n") || undefined,
    );
  },
}));

function showRunError(err: unknown): void {
  if (err instanceof ApiError) {
    if (err.code === "no_engine") {
      void errorDialog(NO_ENGINE_TITLE, err.message);
    } else {
      void errorDialog("Can't start", err.message);
    }
  } else {
    void errorDialog("Can't start", String(err));
  }
}
