import { create } from "zustand";
import { api } from "../api/client";
import type { Meta, RecentItem, Settings, StateSnapshot } from "../api/types";
import { injectPalettes, setTheme } from "../theme";
import { useRun } from "./runStore";

export type View = "transcribe" | "review" | "library";
export type SseStatus = "connecting" | "open" | "down";

interface AppState {
  meta: Meta | null;
  settings: Settings | null;
  snapshot: StateSnapshot | null;
  view: View;
  sse: SseStatus;
  bootError: string | null;

  boot: () => Promise<void>;
  setView: (view: View) => void;
  setSse: (status: SseStatus) => void;
  refreshRecents: () => Promise<void>;
  /** Optimistic local merge + debounced PUT of the full settings dict. */
  updateSettings: (patch: Partial<Settings>) => void;
}

let putTimer: ReturnType<typeof setTimeout> | undefined;

export const useApp = create<AppState>((set, get) => ({
  meta: null,
  settings: null,
  snapshot: null,
  view: "transcribe",
  sse: "connecting",
  bootError: null,

  boot: async () => {
    try {
      const [meta, settings, snapshot] = await Promise.all([
        api.get<Meta>("/api/meta"),
        api.get<Settings>("/api/settings"),
        api.get<StateSnapshot>("/api/state"),
      ]);
      injectPalettes(meta.palettes);
      setTheme(settings.theme);
      set({ meta, settings, snapshot, bootError: null });
      if (snapshot.run) {
        useRun.getState().applyRunState(snapshot.run, { resync: true });
      }
    } catch (err) {
      set({ bootError: err instanceof Error ? err.message : String(err) });
    }
  },

  setView: (view) => set({ view }),
  setSse: (sse) => set({ sse }),

  refreshRecents: async () => {
    const { items } = await api.get<{ items: RecentItem[] }>("/api/recents");
    set((s) =>
      s.snapshot ? { snapshot: { ...s.snapshot, recents: items } } : {},
    );
  },

  updateSettings: (patch) => {
    const settings = get().settings;
    if (!settings) return;
    const next = { ...settings, ...patch };
    set({ settings: next });
    if (patch.theme !== undefined) setTheme(patch.theme);
    clearTimeout(putTimer);
    putTimer = setTimeout(() => {
      api.put<Settings>("/api/settings", next).catch(() => {
        /* transient save failures are retried by the next change;
           the file is also rewritten on every run */
      });
    }, 400);
  },
}));
