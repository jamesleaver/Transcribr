import { create } from "zustand";
import { api } from "../api/client";

/** Mirrors UpdateChecker.status() on the backend. */
export interface UpdateStatus {
  state:
    | "idle"
    | "checking"
    | "current"
    | "available"
    | "downloading"
    | "ready_to_install"
    | "error";
  current: string;
  latest: string | null;
  notes: string;
  url: string;
  pct: number;
  error: string | null;
  can_install: boolean;
}

interface UpdateSlice {
  status: UpdateStatus | null;
  /** Set when the user waves the banner away; kept for this run only. */
  dismissed: boolean;
  apply: (status: UpdateStatus) => void;
  refresh: () => Promise<void>;
  check: () => Promise<void>;
  install: () => Promise<void>;
  dismiss: () => void;
}

export const useUpdate = create<UpdateSlice>((set) => ({
  status: null,
  dismissed: false,
  apply: (status) => set({ status }),
  refresh: async () => {
    try {
      set({ status: await api.get<UpdateStatus>("/api/update") });
    } catch {
      /* the update check is never worth interrupting the app for */
    }
  },
  check: async () => {
    try {
      set({ status: await api.post<UpdateStatus>("/api/update/check") });
    } catch {
      /* ignore - the banner stays as it was */
    }
  },
  install: async () => {
    set({ dismissed: false });
    try {
      set({ status: await api.post<UpdateStatus>("/api/update/install") });
    } catch {
      /* the backend reports failures through the update event */
    }
  },
  dismiss: () => set({ dismissed: true }),
}));
