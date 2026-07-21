import { create } from "zustand";
import { api, ApiError } from "../api/client";
import { playSpan, stopPlayback } from "../audio";
import { choiceDialog, confirmDialog, errorDialog } from "./dialogs";
import { useApp } from "./store";

// The review workspace's state: a mirror of the server-side
// ReviewSession document plus purely client-side concerns (selection,
// edit mode, find state, confidence-shading toggle).

const RETRANS_IDLE = {
  running: false,
  message: "",
  pct: 0,
  indeterminate: false,
  log: "",
};

export interface ReviewParagraph {
  id: number;
  start: number;
  end: number;
  body: string;
  speaker: string | null;
  /** Timestamp state: null = computed time, "hidden", or an amended
   *  time in seconds. */
  ts: "hidden" | number | null;
  /** Looks like engine hallucination (repeated/looping text). */
  suspect: boolean;
  play: { start: number; end: number | null } | null;
  conf: [number, number, "low" | "med"][];
}

export interface AudioStatus {
  state: "probing" | "extracting" | "ready" | "unavailable";
  url?: string;
  duration?: number | null;
  error?: string;
}

export interface ReviewPayload {
  rev: number;
  out_path: string;
  output_format: string;
  show_timestamp: boolean;
  title: string | null;
  loaded: boolean;
  audio: AudioStatus;
  speaker_names: Record<string, string>;
  visible_speakers: number;
  labelled: number;
  total: number;
  can_undo: boolean;
  can_redo: boolean;
  has_word_conf: boolean;
  diarized: boolean;
  verified_by: string | null;
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

  playing: number | null;
  /** True while the active playback runs on past its paragraph. */
  playingThrough: boolean;
  setAudioStatus: (status: AudioStatus) => void;
  /** `through` plays on past the paragraph to the end of the audio. */
  togglePlay: (index: number, through?: boolean) => void;
  locateAudio: () => Promise<void>;

  /** Review-pane saving options; null = keep what the run started with. */
  saveShowTimestamp: boolean | null;
  setSaveShowTimestamp: (value: boolean) => void;
  /** Certifier name for the "verified by" disclaimer; empty = unverified. */
  verifyName: string;
  /** Shift-click range selection for section-level actions. */
  selRange: { from: number; to: number } | null;
  retrans: {
    running: boolean;
    message: string;
    pct: number;
    indeterminate: boolean;
    log: string;
  };
  setVerifyName: (value: string) => void;
  exportAs: (fmt: "pdf") => Promise<void>;

  needsAttention: (index: number) => boolean;
  jumpNextAttention: () => void;
  findNext: () => void;

  setSpeaker: (index: number, slot: string | null) => Promise<void>;
  setTimestamp: (index: number, value: "hidden" | number | null) => Promise<void>;
  selectRangeTo: (index: number) => void;
  jumpNextSuspect: () => void;
  retranscribe: (model: string, condition: boolean) => Promise<void>;
  cancelRetranscribe: () => Promise<void>;
  applyRetrans: (d: {
    state: string;
    message?: string;
    pct?: number;
    indeterminate?: boolean;
    log_delta?: string;
  }) => void;
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

/** After a save/export: open the file, show it in the folder, or move
 *  on - James's post-save flow of choice. */
async function offerOpenOrReveal(
  title: string,
  body: string,
  path: string,
): Promise<void> {
  const i = await choiceDialog(title, body, [
    "Open file",
    "Show in folder",
    "Done",
  ]);
  if (i === 0) await api.post("/api/path/open", { path }).catch(() => {});
  if (i === 1) await api.post("/api/path/reveal", { path }).catch(() => {});
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
      saveShowTimestamp: null,
      verifyName: payload.verified_by ?? "",
      selRange: null,
      retrans: RETRANS_IDLE,
    }),

  closeDoc: () => {
    stopPlayback();
    set({ doc: null, editing: null, searchHit: null, playing: null,
      playingThrough: false });
  },

  playing: null,
  playingThrough: false,

  saveShowTimestamp: null,
  setSaveShowTimestamp: (value) => set({ saveShowTimestamp: value }),
  verifyName: "",
  setVerifyName: (value) => set({ verifyName: value }),

  selRange: null,
  retrans: RETRANS_IDLE,

  selectRangeTo: (index) =>
    set((s) => {
      const n = s.doc?.paragraphs.length ?? 0;
      if (n === 0) return {};
      const anchor = s.selRange
        ? s.selRange.from === clamp(index, n)
          ? s.selRange.to
          : s.selRange.from
        : s.selected;
      const i = clamp(index, n);
      return {
        selRange: { from: Math.min(anchor, i), to: Math.max(anchor, i) },
        selected: i,
      };
    }),

  retranscribe: async (model, condition) => {
    const s = get();
    const doc = s.doc;
    if (!doc || s.retrans.running) return;
    const range = s.selRange ?? { from: s.selected, to: s.selected };
    try {
      set({
        retrans: {
          running: true,
          message: "Starting…",
          pct: 0,
          indeterminate: true,
          log: "",
        },
      });
      await api.post("/api/review/retranscribe", {
        rev: doc.rev,
        from: range.from,
        to: range.to,
        ...(model ? { model } : {}),
        condition,
      });
    } catch (err) {
      set({ retrans: RETRANS_IDLE });
      if (err instanceof ApiError) {
        void errorDialog("Cannot re-transcribe", err.message);
      } else {
        throw err;
      }
    }
  },

  cancelRetranscribe: async () => {
    await api.post("/api/review/retranscribe/cancel", {}).catch(() => {});
  },

  applyRetrans: (d) => {
    const prev = get().retrans;
    if (d.state === "running") {
      set({
        retrans: {
          running: true,
          message: d.message ? d.message : prev.message,
          pct: d.pct ?? prev.pct,
          indeterminate: d.indeterminate ?? prev.indeterminate,
          log: d.log_delta
            ? (prev.log + d.log_delta).slice(-100_000)
            : prev.log,
        },
      });
      return;
    }
    // Terminal: keep the log visible; the message reports the outcome.
    set({
      retrans: {
        running: false,
        message: d.message ?? "",
        pct: d.state === "done" ? 100 : prev.pct,
        indeterminate: false,
        log: prev.log,
      },
    });
    if (d.state === "done") {
      set({ selRange: null });
      void get().refetch();
    }
  },

  exportAs: async (fmt) => {
    const s = get();
    const doc = s.doc;
    if (!doc) return;
    try {
      const res = await api.post<{ out_path: string }>(
        "/api/review/export",
        {
          rev: doc.rev,
          format: fmt,
          ...(s.saveShowTimestamp !== null
            ? { show_timestamp: s.saveShowTimestamp }
            : {}),
          verified_by: s.verifyName,
        },
      );
      void offerOpenOrReveal("Exported",
        `Written to:\n${res.out_path}\n\nThe review stays open.`,
        res.out_path);
    } catch (err) {
      if (err instanceof ApiError) {
        void errorDialog("Export failed", err.message);
      } else {
        throw err;
      }
    }
  },

  locateAudio: async () => {
    const picked = await api.post<{ path?: string; cancelled?: boolean }>(
      "/api/pick",
      { kind: "media" },
    );
    if (!picked.path) return;
    try {
      await api.post("/api/review/audio", { path: picked.path });
    } catch (err) {
      if (err instanceof ApiError) {
        void errorDialog("Can't use that file", err.message);
      } else {
        throw err;
      }
    }
  },

  setAudioStatus: (status) =>
    set((s) => {
      if (!s.doc) return {};
      if (status.state !== "ready" && s.playing !== null) stopPlayback();
      return { doc: { ...s.doc, audio: status } };
    }),

  togglePlay: (index, through = false) => {
    const s = get();
    const doc = s.doc;
    if (!doc) return;
    // Pressing the same control again stops that mode of playback.
    if (s.playing === index && s.playingThrough === through) {
      stopPlayback();
      set({ playing: null, playingThrough: false });
      return;
    }
    const span = doc.paragraphs[index]?.play;
    if (!span || doc.audio.state !== "ready" || !doc.audio.url) return;
    stopPlayback();
    set({ playing: index, playingThrough: through });
    playSpan(
      doc.audio.url,
      through ? { start: span.start, end: null } : span,
      () => {
        if (useReview.getState().playing === index)
          set({ playing: null, playingThrough: false });
      },
    );
  },

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
    set((s) => ({
      selected: clamp(index, s.doc?.paragraphs.length ?? 0),
      selRange: null,
    })),

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

  jumpNextSuspect: () => {
    const s = get();
    const paras = s.doc?.paragraphs ?? [];
    const n = paras.length;
    if (n === 0) return;
    // Find the next suspect paragraph after the current selection,
    // then select the whole contiguous suspect run so one
    // re-transcription covers it. Wraps around.
    const anchor = (s.selRange?.to ?? s.selected) + 1;
    for (let off = 0; off < n; off++) {
      const i = (anchor + off) % n;
      if (paras[i].suspect) {
        let lo = i;
        let hi = i;
        while (lo > 0 && paras[lo - 1].suspect) lo -= 1;
        while (hi < n - 1 && paras[hi + 1].suspect) hi += 1;
        set({
          selected: lo,
          selRange: lo === hi ? null : { from: lo, to: hi },
        });
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

  setTimestamp: async (index, value) => {
    applyResult(await mutateApi("timestamp", { index, value }));
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
    const s = get();
    const doc = s.doc;
    if (!doc) return;
    try {
      // The save format follows the Settings page's default (docx or
      // txt); one-off PDFs use the Export button instead.
      const fmt = useApp.getState().settings?.output_format;
      const saved = await api.post<{ out_path: string }>("/api/review/save", {
        rev: doc.rev,
        mode,
        ...(fmt ? { format: fmt } : {}),
        ...(s.saveShowTimestamp !== null
          ? { show_timestamp: s.saveShowTimestamp }
          : {}),
        verified_by: s.verifyName,
      });
      if (s.saveShowTimestamp !== null)
        useApp.getState().updateSettings({
          show_timestamp: s.saveShowTimestamp,
        });
      get().closeDoc();
      useApp.getState().setView("transcribe");
      void offerOpenOrReveal("Saved", `Written to:\n${saved.out_path}`,
        saved.out_path);
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
