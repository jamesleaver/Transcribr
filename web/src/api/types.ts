// Mirrors the JSON contracts served by transcribr.py. This file is the
// single place both sides are reviewed against.

export type ThemeSetting = "auto" | "light" | "dark";

/** Transcript-semantic colours served from Python's _PALETTES. */
export interface Palette {
  text_bg: string;
  text_fg: string;
  insert: string;
  speaker_fg: string;
  timestamp_fg: string;
  selected_bg: string;
  editing_bg: string;
  search_bg: string;
  conf_low: string;
  conf_med: string;
  badge_fg: string;
  speaker_colours: Record<string, string>; // "1".."9"
  drop_bg: string;
  drop_hover: string;
  drop_border: string;
  drop_fg: string;
}

/** One entry of the simplified three-choice model picker. */
export interface ModelTier {
  id: string;
  label: string;
  model: string;
  model_en: string;
  size: string;
  note: string;
  recommended?: boolean;
}

export interface Meta {
  version: string;
  about_text: string;
  platform: string;
  reveal_label: string;
  ui_mode: "webview" | "browser";
  engines: { key: string; name: string }[];
  models: string[];
  model_tiers: ModelTier[];
  languages: [string, string | null][];
  palettes: { light: Palette; dark: Palette };
  ffmpeg: boolean;
  pyav: boolean;
  diarize_available: boolean;
  diarize_models: { id: string; label: string; note: string; size: string }[];
  /** True when the backend was started with --annotate (dev builds). */
  annotate: boolean;
  readme_available: boolean;
}

/** Same keys and value conventions as settings.json (shared with Tk). */
export interface Settings {
  engine: string;
  model: string;
  language: string;
  task: "transcribe" | "translate";
  output_format: "txt" | "docx" | "pdf";
  /** Document title (heading). Never sent to the engine. */
  title: string;
  /** Optional initial_prompt / vocabulary hint. Opt-in; can backfire. */
  prompt: string;
  gap: number;
  show_timestamp: boolean;
  temperature: number;
  beam_size: number;
  best_of: number;
  compression_ratio_threshold: number;
  logprob_threshold: number;
  no_speech_threshold: number;
  condition_on_previous_text: boolean;
  extra_json: boolean;
  extra_srt: boolean;
  extra_vtt: boolean;
  extra_tsv: boolean;
  theme: ThemeSetting;
  show_details: boolean;
  diarize: boolean;
  num_speakers: number;
  diarize_model: string;
  diarize_threshold: number;
  show_all_models: boolean;
  /** Show the context/vocabulary priming field on the Transcribe page. */
  show_prompt: boolean;
  /** Show the experimental speaker-detection card on the Transcribe page. */
  show_diarize: boolean;
}

export interface RecentItem {
  path: string;
  name: string;
  exists: boolean;
}

export type RunPhase =
  | "idle"
  | "running"
  | "stopping"
  | "done"
  | "error"
  | "cancelled";

export type ProgressStage =
  | "downloading"
  | "loading"
  | "transcribing";

export interface Progress {
  pct: number;
  status_text: string;
  /** Which phase of the run this readout describes. Absent on older
   *  snapshots and on the terminal done/error progress. */
  stage?: ProgressStage;
  /** True while the percentage is not meaningful (model loading / start of
   *  transcription); the UI shows an animated indeterminate bar. */
  indeterminate?: boolean;
}

export interface RunState {
  phase: RunPhase;
  file: string | null;
  run_id: number;
  batch: { index: number; total: number } | null;
  out_path: string | null;
  progress?: Progress | null;
  /** Log tail, present only in /api/state resync snapshots. */
  log?: string;
  /** Present on run_state SSE events for phase "error". */
  message?: string;
  first_line?: string;
}

export interface BatchDone {
  stopped: boolean;
  succeeded: string[];
  failed: [string, string][];
}

export interface InspectedFile {
  path: string;
  name: string;
  exists: boolean;
  derived_output: string;
}

// ---- Model manager --------------------------------------------------

export interface ModelInfo {
  model: string;
  /** Other names for the same weights (e.g. "large" for "large-v3"). */
  aliases: string[];
  installed: boolean;
  /** On-disk size in bytes (0 when not installed). */
  size: number;
  /** Storage identity (filename or HF repo); aliases share one. */
  storage_key: string;
  /** True for a cached model that isn't in the built-in list. */
  custom: boolean;
}

export interface EngineModels {
  key: string;
  name: string;
  /** HF-backed engines can fetch brand-new models by name/repo. */
  supports_custom: boolean;
  models: ModelInfo[];
  /** Bytes this engine's cached models occupy (aliases counted once). */
  total: number;
  /** True if the app can pip-remove this engine to reclaim space. */
  removable: boolean;
}

/** An engine the app can pip-install on demand (e.g. openai-whisper). */
export interface InstallableEngine {
  key: string;
  name: string;
  note: string;
  approx_mb: number;
}

export interface ModelJob {
  /** "download" for model weights, "engine" for a pip install/uninstall. */
  kind?: "download" | "engine";
  action?: "install" | "uninstall";
  engine: string;
  model: string;
  phase: string;
  pct: number;
  downloaded?: number;
  total?: number;
  speed?: number;
  status_text: string;
}

export interface ModelsPayload {
  whisper_cache: string;
  hf_cache: string;
  engines: EngineModels[];
  /** Engines not installed that the app can pip-install on demand. */
  installable: InstallableEngine[];
  total: number;
  /** The in-flight job, if any (also delivered via model_progress). */
  job: ModelJob | null;
  /** True while a download/engine op or a transcription is running. */
  busy: boolean;
}

export interface ModelDone {
  ok: boolean;
  model: string;
  engine?: string;
  error?: string;
  cancelled?: boolean;
}

export interface StateSnapshot {
  run: RunState | null;
  review: unknown | null;
  autosave_pending: boolean;
  recents: RecentItem[];
}
