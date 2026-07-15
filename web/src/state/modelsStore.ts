import { create } from "zustand";
import { api, ApiError } from "../api/client";
import type { ModelDone, ModelJob, ModelsPayload } from "../api/types";
import { alertDialog, confirmDialog, errorDialog } from "./dialogs";

// State + actions for the Models surface: the cached-model inventory
// (mirrored from the server), the in-flight download job, and the
// download / cancel / uninstall flows. The server is the source of truth;
// `models` / `model_progress` / `model_done` SSE events keep us in sync.

function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export { fmtBytes };

interface ModelsSlice {
  data: ModelsPayload | null;
  job: ModelJob | null;
  loaded: boolean;
  loading: boolean;

  refresh: () => Promise<void>;
  applyPayload: (p: ModelsPayload) => void;
  applyProgress: (j: ModelJob | Record<string, never>) => void;
  onDone: (d: ModelDone) => void;

  download: (engine: string, model: string) => Promise<void>;
  cancel: () => Promise<void>;
  uninstall: (engine: string, model: string, size: number) => Promise<void>;
}

export const useModels = create<ModelsSlice>((set) => ({
  data: null,
  job: null,
  loaded: false,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const data = await api.get<ModelsPayload>("/api/models");
      set({ data, job: data.job, loaded: true });
    } finally {
      set({ loading: false });
    }
  },

  applyPayload: (p) => set({ data: p, job: p.job, loaded: true }),

  // model_progress carries the job dict, or {} once the job clears.
  applyProgress: (j) =>
    set({ job: "model" in j ? (j as ModelJob) : null }),

  onDone: (d) => {
    if (d.error) {
      void errorDialog(
        "Download failed",
        `Could not download “${d.model}”.`,
        d.error,
      );
    } else if (d.cancelled) {
      void alertDialog("Download cancelled", `Stopped downloading “${d.model}”.`);
    }
    // The follow-up `models` event refreshes the inventory.
  },

  download: async (engine, model) => {
    const name = model.trim();
    if (!name) return;
    try {
      await api.post("/api/models/download", { engine, model: name });
    } catch (err) {
      showModelError(err, "Can't start download");
    }
  },

  cancel: async () => {
    try {
      await api.post("/api/models/download/cancel");
    } catch {
      /* nothing in flight; SSE will settle the state */
    }
  },

  uninstall: async (engine, model, size) => {
    const ok = await confirmDialog({
      title: "Uninstall model?",
      body:
        `Delete the cached “${model}” weights for this engine` +
        (size > 0 ? ` and free ${fmtBytes(size)}` : "") +
        "?\n\nIt will be downloaded again the next time you use it.",
      confirmLabel: "Uninstall",
      defaultAnswer: false,
    });
    if (!ok) return;
    try {
      await api.post("/api/models/uninstall", { engine, model });
    } catch (err) {
      showModelError(err, "Can't uninstall");
    }
  },
}));

function showModelError(err: unknown, fallbackTitle: string): void {
  if (err instanceof ApiError) {
    const title =
      err.code === "busy" || err.code === "model_busy" ? "Busy" : fallbackTitle;
    void errorDialog(title, err.message);
  } else {
    void errorDialog(fallbackTitle, String(err));
  }
}
