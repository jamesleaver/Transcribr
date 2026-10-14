// Mirrors the JSON contracts served by the transcribr package.
//
// The shapes Python already owns - Settings, Palette, ThemeSetting,
// RunPhase - are generated from it into contract.generated.ts and
// re-exported here, so a renamed settings key is a build error instead
// of an undefined in the browser. Regenerate with:
//
//     python3 -m transcribr.contract > web/src/api/contract.generated.ts
//
// Everything below is assembled by a route handler rather than declared
// in Python, so it is still reviewed by hand against the routes.

import type { Palette, RunPhase } from "./contract.generated";

export type { Palette, RunPhase, Settings, ThemeSetting } from "./contract.generated";
export { SETTING_CHOICES } from "./contract.generated";

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

/** A recording-type preset: what it sets, and why. */
export interface Preset {
  id: string;
  label: string;
  note: string;
  model: string;
  model_en: string;
  gap: number;
  num_speakers: number;
  voice_breaks: boolean;
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
  presets: Preset[];
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

export interface RecentItem {
  path: string;
  name: string;
  exists: boolean;
  reviewed: boolean;
  verified: boolean;
}

export type ProgressStage =
  | "downloading"
  | "loading"
  | "transcribing"
  // The repair pass re-reading the sections that looked like engine
  // error. Sent by _run_repair; it was missing here until 0.9.16.
  | "retranscribing"
  // Speaker detection, which runs alongside the transcription now.
  | "diarizing";

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
