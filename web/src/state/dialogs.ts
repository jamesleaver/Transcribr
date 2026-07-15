import { create } from "zustand";

// Promise-based modal dialogs - the web replacement for tkinter's
// messagebox.askyesno / showerror / showinfo. One dialog at a time,
// queued if another is already up.

export interface DialogSpec {
  title: string;
  body: string;
  /** Extra lines rendered in a monospace block (file listings). */
  detail?: string;
  kind: "confirm" | "alert" | "error";
  confirmLabel?: string;
  cancelLabel?: string;
  /** Which button is focused initially (Tk's default="no" semantics). */
  defaultAnswer?: boolean;
}

interface Pending {
  spec: DialogSpec;
  resolve: (answer: boolean) => void;
}

interface DialogState {
  current: Pending | null;
  queue: Pending[];
  show: (spec: DialogSpec) => Promise<boolean>;
  answer: (value: boolean) => void;
}

export const useDialogs = create<DialogState>((set, get) => ({
  current: null,
  queue: [],

  show: (spec) =>
    new Promise<boolean>((resolve) => {
      const pending = { spec, resolve };
      if (get().current) {
        set((s) => ({ queue: [...s.queue, pending] }));
      } else {
        set({ current: pending });
      }
    }),

  answer: (value) => {
    const { current, queue } = get();
    if (!current) return;
    current.resolve(value);
    set({ current: queue[0] ?? null, queue: queue.slice(1) });
  },
}));

export const confirmDialog = (spec: Omit<DialogSpec, "kind">) =>
  useDialogs.getState().show({ ...spec, kind: "confirm" });

export const alertDialog = (
  title: string,
  body: string,
  detail?: string,
): Promise<boolean> =>
  useDialogs.getState().show({ title, body, detail, kind: "alert" });

export const errorDialog = (
  title: string,
  body: string,
  detail?: string,
): Promise<boolean> =>
  useDialogs.getState().show({ title, body, detail, kind: "error" });
