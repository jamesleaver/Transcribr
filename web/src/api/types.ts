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

export interface Meta {
  version: string;
  about_text: string;
  platform: string;
  reveal_label: string;
  ui_mode: "webview" | "browser";
  engines: { key: string; name: string }[];
  models: string[];
  languages: [string, string | null][];
  palettes: { light: Palette; dark: Palette };
  ffmpeg: boolean;
  readme_available: boolean;
}

/** Same keys and value conventions as settings.json (shared with Tk). */
export interface Settings {
  engine: string;
  model: string;
  language: string;
  task: "transcribe" | "translate";
  output_format: "txt" | "docx" | "pdf";
  prompt: string;
  gap: number;
  show_timestamp: boolean;
  review: boolean;
  temperature: number;
  beam_size: number;
  best_of: number;
  compression_ratio_threshold: number;
  logprob_threshold: number;
  no_speech_threshold: number;
  condition_on_previous_text: boolean;
  word_timestamps: boolean;
  extra_json: boolean;
  extra_srt: boolean;
  extra_vtt: boolean;
  extra_tsv: boolean;
  highlight_confidence: boolean;
  theme: ThemeSetting;
  show_details: boolean;
}

export interface RecentItem {
  path: string;
  name: string;
  exists: boolean;
}

export interface StateSnapshot {
  run: unknown | null;
  review: unknown | null;
  autosave_pending: boolean;
  recents: RecentItem[];
}
