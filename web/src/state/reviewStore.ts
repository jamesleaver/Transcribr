import { create } from "zustand";
import { api, ApiError } from "../api/client";
import { confirmDialog, errorDialog } from "./dialogs";
import { useApp } from "./store";

// The review workspace's state: a mirror of the server-side
// ReviewSession document plus purely client-side concerns (selection,
// edit mode, find state, confidence-shading toggle).

export interface ReviewParagraph {
  id: number;
  start: number;
  end: number;
  body: string;
  speaker: string | null;
  play: { start: number; end: number | null } | null;
  conf: [number, number, "low" | "med"][];
}

export interface ReviewPayload {
  rev: number;
  out_path: string;
  output_format: string;
  show_timestamp: boolean;
  title: string | null;
  loaded: boolean;
  audio: { state: string };
  speaker_names: Record<string, string>;
  visible_speakers: number;
  labelled: number;
  total: number;
  can_undo: boolean;
  can_redo: boolean;
  has_word_conf: boolean;
  paragraphs: ReviewParagraph[];
  new_index?: number | null;
  count?: number;
}

interface SlimDelta {
  rev: number;
  labelled: number;
  total: number;
  visible_speakers: number;
  index?: number;
  speaker?: string | null;
  speaker_names?: Record<string, string>;
}

export type SearchHit = { index: number; start: number; end: number } | null;

interface ReviewSlice {
  doc: ReviewPayload | null;
  selected: number;
  editing: number | null;
  editingDraft: string;
  showConfidence: boolean;
  findTerm: string;
  replaceTerm: string;
  matchCase: boolean;
  findStatus: string;
  searchHit: SearchHit;

  openDoc: (payload: ReviewPayload) => void;
  closeDoc: () => void;
  refetch: () => Promise<void>;

  select: (index: number) => void;
  startEdit: (index: number) => void;
  cancelEdit: () => void;
  setEditingDraft: (value: string) => void;
  /** Commit the in-flight edit, if any (used before saves/clicks). */
  commitPendingEdit: () => Promise<void>;
  setShowConfidence: (value: boolean) => void;
  setFindTerm: (value: string) => void;
  setReplaceTerm: (value: string) => void;
  setMatchCase: (value: boolean) => void;

  needsAttention: (index: number) => boolean;
  jumpNextAttention: () => void;
  findNext: () => void;

  setSpeaker: (index: number, slot: string | null) => Promise<void>;
  setSpeakerName: (slot: string, name: string) => Promise<void>;
  addSpeaker: () => Promise<void>;
  commitEdit: (index: number, text: string) => Promise<void>;
  split: (index: number, offset: number) => Promise<void>;
  merge: (index: number) => Promise<void>;
  replaceAll: () => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  save: (mode: "labels" | "no_labels" | "revision") => Promise<void>;
  closeWithoutSaving: () => Promise<void>;
}

function clamp(i: number, n: number): number {
  return Math.max(0, Math.min(i, n - 1));
}

async function mutateApi(
  action: string,
  body: Record<string, unknown>,
): Promise<ReviewPayload | SlimDelta | null> {
  const doc = useReview.getState().doc;
  if (!doc) return null;
  try {
    return await api.post<ReviewPayload | SlimDelta>(`/api/review/${action}`, {
      rev: doc.rev,
      ...body,
    });
  } catch (err) {
    if (err instanceof ApiError && err.code === "stale_rev") {
      await useReview.getState().refetch();
      return null;
    }
    throw err;
  }
}

function isFull(r: ReviewPayload | SlimDelta): r is ReviewPayload {
  return (r as ReviewPayload).paragraphs !== undefined;
}

function applyResult(r: ReviewPayload | SlimDelta | null): void {
  if (!r) return;
  useReview.setState((s) => {
    if (!s.doc) return {};
    if (isFull(r)) {
      return { doc: { ...r }, selected: clamp(s.selected, r.paragraphs.length) };
    }
    const doc = { ...s.doc, rev: r.rev, labelled: r.labelled, total: r.total,
      visible_speakers: r.visible_speakers };
    if (r.speaker_names) doc.speaker_names = r.speaker_names;
    if (r.index !== undefined && r.speaker !== undefined) {
      doc.paragraphs = doc.paragraphs.map((p, i) =>
        i === r.index ? { ...p, speaker: r.speaker ?? null } : p,
      );
    }
    return { doc };
  });
}

export const useReview = create<ReviewSlice>((set, get) => ({
  doc: null,
  selected: 0,
  editing: null,
  editingDraft: "",
  showConfidence: true,
  findTerm: "",
  replaceTerm: "",
  matchCase: false,
  findStatus: "",
  searchHit: null,

  openDoc: (payload) =>
    set({
      doc: payload,
      selected: 0,
      editing: null,
      findStatus: "",
      searchHit: null,
      showConfidence: payload.has_word_conf,
    }),

  closeDoc: () => set({ doc: null, editing: null, searchHit: null }),

  refetch: async () => {
    try {
      const payload = await api.get<ReviewPayload>("/api/review");
      set((s) => ({
        doc: payload,
        selected: clamp(s.selected, payload.paragraphs.length),
        editing: null,
      }));
    } catch (err) {
      if (err instanceof ApiError && err.code === "no_review") {
        get().closeDoc();
      }
    }
  },

  select: (index) =>
    set((s) => ({ selected: clamp(index, s.doc?.paragraphs.length ?? 0) })),

  startEdit: (index) =>
    set((s) => ({
      editing: index,
      editingDraft: s.doc?.paragraphs[index]?.body ?? "",
      selected: index,
    })),

  cancelEdit: () => set({ editing: null, editingDraft: "" }),

  setEditingDraft: (value) => set({ editingDraft: value }),

  commitPendingEdit: async () => {
    const s = get();
    if (s.editing === null) return;
    await s.commitEdit(s.editing, s.editingDraft);
  },

  setShowConfidence: (value) => set({ showConfidence: value }),
  setFindTerm: (value) => set({ findTerm: value, findStatus: "", searchHit: null }),
  setReplaceTerm: (value) => set({ replaceTerm: value }),
  setMatchCase: (value) => set({ matchCase: value, findStatus: "", searchHit: null }),

  needsAttention: (index) => {
    const s = get();
    const p = s.doc?.paragraphs[index];
    if (!p) return false;
    if (p.speaker === null) return true;
    if (s.showConfidence && s.doc?.has_word_conf) return p.conf.length > 0;
    return false;
  },

  jumpNextAttention: () => {
    const s = get();
    const n = s.doc?.paragraphs.length ?? 0;
    if (n === 0) return;
    const start = s.selected + 1;
    for (let off = 0; off < n; off++) {
      const i = (start + off) % n;
      if (s.needsAttention(i)) {
        set({ selected: i });
        return;
      }
    }
  },

  findNext: () => {
    const s = get();
    const doc = s.doc;
    const term = s.findTerm;
    if (!doc || !term) return;
    const haystackOf = (b: string) => (s.matchCase ? b : b.toLowerCase());
    const needle = s.matchCase ? term : term.toLowerCase();
    const n = doc.paragraphs.length;
    const from = s.searchHit
      ? { index: s.searchHit.index, offset: s.searchHit.end }
      : { index: 0, offset: 0 };

    const scan = (index: number, offset: number): SearchHit => {
      for (let i = index; i < n; i++) {
        const pos = haystackOf(doc.paragraphs[i].body).indexOf(
          needle,
          i === index ? offset : 0,
        );
        if (pos >= 0) return { index: i, start: pos, end: pos + term.length };
      }
      return null;
    };

    let hit = scan(from.index, from.offset);
    let wrapped = false;
    if (!hit && (from.index > 0 || from.offset > 0)) {
      hit = scan(0, 0);
      wrapped = true;
    }
    if (!hit) {
      set({ findStatus: "Not found", searchHit: null });
      return;
    }
    set({ searchHit: hit, selected: hit.index, findStatus: wrapped ? "Wrapped" : "" });
  },

  setSpeaker: async (index, slot) => {
    applyResult(await mutateApi("speaker", { index, slot }));
    // Auto-advance to the next paragraph (parity with _kb_set_speaker).
    set((s) => ({ selected: clamp(index + 1, s.doc?.paragraphs.length ?? 0) }));
  },

  setSpeakerName: async (slot, name) => {
    applyResult(await mutateApi("speaker-name", { slot, name }));
  },

  addSpeaker: async () => {
    const doc = get().doc;
    if (!doc) return;
    applyResult(
      await mutateApi("visible-speakers", { n: doc.visible_speakers + 1 }),
    );
  },

  commitEdit: async (index, text) => {
    set({ editing: null });
    applyResult(await mutateApi("edit", { index, text }));
  },

  split: async (index, offset) => {
    const r = await mutateApi("split", { index, offset });
    applyResult(r);
    if (r && isFull(r) && r.new_index != null) set({ selected: r.new_index });
  },

  merge: async (index) => {
    if (index <= 0) return;
    applyResult(await mutateApi("merge", { index }));
    set({ selected: index - 1 });
  },

  replaceAll: async () => {
    const s = get();
    if (!s.findTerm) return;
    const r = await mutateApi("replace-all", {
      find: s.findTerm,
      replace: s.replaceTerm,
      match_case: s.matchCase,
    });
    applyResult(r);
    if (r && isFull(r)) {
      set({
        findStatus: r.count ? `Replaced ${r.count}` : "No matches",
        searchHit: null,
      });
    }
  },

  undo: async () => {
    if (get().editing !== null) return;   // commit/cancel first (parity)
    applyResult(await mutateApi("undo", {}));
  },

  redo: async () => {
    if (get().editing !== null) return;
    applyResult(await mutateApi("redo", {}));
  },

  save: async (mode) => {
    const doc = get().doc;
    if (!doc) return;
    try {
      await api.post<{ out_path: string }>("/api/review/save", {
        rev: doc.rev,
        mode,
      });
      get().closeDoc();
      useApp.getState().setView("transcribe");
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale_rev") {
        await get().refetch();
        void errorDialog("Document changed", "The document changed — please retry the save.");
      } else if (err instanceof ApiError) {
        void errorDialog(
          err.code === "missing_dependency" ? "Cannot write file" : "Save failed",
          err.message,
        );
      } else {
        throw err;
      }
    }
  },

  closeWithoutSaving: async () => {
    const doc = get().doc;
    if (!doc) return;
    const ok = await confirmDialog({
      title: "Close without saving?",
      body: "Your speaker labels and edits will be discarded. The file on disk stays as it was.",
      confirmLabel: "Close without saving",
      defaultAnswer: false,
    });
    if (!ok) return;
    await api.post("/api/review/close", { rev: doc.rev });
    get().closeDoc();
    useApp.getState().setView("transcribe");
  },
}));
