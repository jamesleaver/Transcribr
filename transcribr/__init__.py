"""
Transcribr - GUI for transcribing audio/video files with Whisper,
grouping the result into paragraphs, and reviewing/labelling speakers.

(c) Fisheggs Pty Ltd, 2026. This software is experimental. Everything runs
locally on your computer: a Whisper engine (faster-whisper by default;
openai-whisper and mlx-whisper are used when installed) transcribes
the audio, an optional speaker-detection pass works out who is
speaking, the result is grouped into paragraphs, and a review pane
lets you check the speaker labels, edit text, and listen back to the
source audio before saving as .docx, .pdf, or .txt. Several files can
be queued and transcribed in one unattended batch. When a particular
model is run for the first time it is downloaded and stored locally;
'large-v3-turbo' (the Standard choice) is recommended. Use at your own
risk.
Questions: hello@fisheggs.au.

Run with:
    python3 -m transcribr
"""

# Transcribr was a single 8,400-line module until 0.9.15. Every name it
# exposed at the top level is re-exported here, so `import transcribr`
# still reaches all of it and nothing outside the package had to change.
# The submodules are importable too: that is how the tests reach state a
# `from ... import` would have frozen.

from . import (
    version,
    errors,
    palettes,
    speakers,
    platformutil,
    paragraphs,
    live,
    writers,
    readers,
    engines,
    diarize,
    worker,
    workerproc,
    transcript,
    settings,
    broker,
    runctl,
    models,
    updates,
    audioprep,
    review,
    server,
    cli,
)

from .version import ABOUT_TEXT, __version__
from .errors import (
    ApiFail, DiarizationUnavailable, TranscriptParseError, _CancelledByUser,
    _EngineNotAvailable
)
from .palettes import _ACTIVE_THEME, _PALETTES, _palette, _resolve_theme
from .speakers import (
    MAX_SPEAKERS, SPEAKER_SLOTS, UNATTRIBUTED_LABEL, _export_speaker_colour
)
from .platformutil import (
    ANNOTATE_MODE, _ANNOTATION_FIELDS, _AUDIO_EXTENSIONS, _MIGRATIONS,
    _RECENT_MAX, _REVEAL_LABEL, _SCHEMA, _annotations_file,
    _annotations_load, _annotations_lock, _annotations_save,
    _autosave_clear, _autosave_file, _autosave_load, _autosave_save,
    _config_dir, _find_readme, _guess_audio_for_transcript,
    _install_crash_logging, _log, _log_dir, _log_file_path,
    _migrate_recent_0_to_1, _next_revision_path, _open_path, _open_url,
    _read_versioned, _recent_add, _recent_file, _recent_load, _recent_save,
    _reveal_path, _settings_file, _settings_load, _settings_save,
    _terms_add, _terms_all, _terms_folder_key, _terms_load, _terms_path,
    _terms_save, _unwrap_versioned, _write_json_atomic, _write_versioned,
    annotate_enabled, annotation_add, annotation_delete, annotations_clear,
    set_annotate_mode
)
from .paragraphs import (
    AUDIO_SAMPLE_RATE, ParagraphStream, SHORT_RESPONSES,
    _GAP_SAMPLE_SEGMENTS, _INTERRUPTION_ENDINGS, _NON_AUDIO_SIGNATURES,
    _PARAGRAPH_SECONDS_CAP, _SOFT_BREAK_FRACTION, _SOFT_BREAK_MIN_SECONDS,
    _disclaimer_text, _ffprobe_duration, _format_duration,
    _gaps_are_informative, _have_pyav, _is_sentence_continuation,
    _is_short_response, _leading_acknowledgment, _pyav_duration,
    _refine_segment_times, _should_break, decode_audio_16k,
    describe_non_audio_file, format_timestamp, get_audio_duration,
    paragraphify, render
)
from .live import LiveTranscript, no_audio_marker, segment_is_phantom
from .writers import (
    DOC_TEMPLATES, DOCX_FONT, DOCX_FONT_PT, DOCX_STYLE_DISCLAIMER,
    DOCX_STYLE_FOOTER,
    DOCX_STYLE_SPEAKER, DOCX_STYLE_TEXT, DOCX_STYLE_TITLE,
    _docx_build_styles, _docx_fix_font, _docx_prune_unused_styles,
    _docx_shade_style, _format_srt_time, _format_vtt_time, _reviewed_result,
    _seg_field, _write_docx, _write_extra_formats, _write_json_result,
    _write_pdf, _write_srt, _write_tsv, _write_vtt, write_paragraphs_to_file
)
from .readers import (
    SEARCH_FILE_LIMIT, SEARCH_HITS_PER_FILE, _DISCLAIMER_RE,
    _TRANSCRIPT_GLOBS, _TS_PREFIX_RE, _infer_paragraph_end_times,
    _parse_docx_transcript, _parse_timestamp_prefix, _parse_txt_transcript,
    _search_candidates, read_paragraphs_from_file, search_transcripts
)
from .presets import (                                     # noqa: F401
    PRESETS, PRESET_KEYS, matching_preset, preset, settings_for
)
from .engines import (
    ENGINE_AUTO_NAME, LANGUAGES, MODEL_TIERS, WHISPER_MODELS,
    _AVAILABLE_ENGINES, _MLX_REPO_MAP, _MODEL_ALIASES, _canonical_model,
    _detect_engines, _macos_supports_mlx, _mlx_repo_for, _redetect_engines,
    available_engines, resolve_engine_key
)
from .diarize import (
    DIARIZE_DEFAULT_VOICE_MODEL, DIARIZE_VOICE_MODELS,
    _DIARIZE_CLUSTER_THRESHOLD, _DIARIZE_MIN_COVERAGE,
    _DIARIZE_MIN_MAJORITY, _DIARIZE_SEGMENTATION, _diarize_models_dir,
    _download_one_model, _run_diarization, _voice_model_spec,
    diarize_models_ready, ensure_diarize_models, label_paragraphs
)
from .worker import (
    HALLUCINATION_COVERAGE, HALLUCINATION_LOWCONF_FRACTION,
    HALLUCINATION_LOWCONF_MINWORDS, HALLUCINATION_LOWCONF_PROB,
    HALLUCINATION_MIN_REPEATS, SILENCE_MIN_SPAN_SECS, SILENCE_RMS,
    SILENCE_WINDOW_SECS, _DownloadMonitor, _MODEL_APPROX_MB,
    _ProgressWriter, _QueueWriter, _SEGMENT_LINE_RE, _SENTENCE_SPLIT_RE,
    _TERM_STOPWORDS, _TERM_TITLES, _TERM_WORD_RE, _WORD_RE, _capitalised,
    _drop_segments_in_silence, _extract_word_conf, _flush_run,
    _has_repetition_loop, _humanize_bytes, _is_fully_periodic,
    _looks_like_same_term, _mostly_low_confidence, _normalise_words,
    _paragraphs_with_speakers, _run_faster_whisper, _run_mlx_whisper,
    _run_openai_whisper, _splice_no_audio_markers, _term_key,
    detect_hallucinations, detect_silences, extract_terms, transcribe_worker
)
from .workerproc import (
    CANCEL_GRACE_SECONDS, SILENT_DEATH_SECONDS, TERMINATE_GRACE_SECONDS,
    _CHILD_FINISHED, _CRASH_ADVICE, _PipeQueue, _SIGNAL_NOTES, _TERMINAL,
    _WINDOWS_FAULTS, _child_main, _describe_exit, _signal_notes, _stop,
    default_worker_fn, engine_runs_in_process, run_worker_subprocess
)
from .transcript import (
    TranscriptModel, _resolve_word_timestamps, build_worker_params,
    compose_initial_prompt
)
from .settings import (
    _SETTINGS_BOOL_KEYS, _SETTINGS_NUMBER_KEYS, _settings_choices,
    current_settings, default_settings, validate_settings
)
from .broker import EventBroker, _sse_format
from .runctl import (
    RUN_PHASES, RunController, _MEDIA_EXTENSIONS, _NO_ENGINE_HINT,
    ensure_output_is_safe
)
from .models import (
    ModelController, ModelStore, _INSTALLABLE_ENGINES, _WHISPER_PT_FALLBACK,
    _dedupe_alias_models, _download_status_text, _engine_install_args,
    _engine_uninstall_pkgs, _faster_repo_for, _hf_cache_dir,
    _hf_delete_repo, _hf_repo_sizes, _pip_stream, _prefetch_model,
    _repo_belongs_to_engine, _run_engine_op, _uninstall_model,
    _whisper_cache_dir, _whisper_pt_filename
)
from .updates import (
    UpdateChecker, _ALL_ARCH_TOKENS, _ARCH_TOKENS, _ASSET_PREFERENCE,
    _PLATFORM_INSTALLER, _UPDATE_API, _UPDATE_RELEASES_URL, _UPDATE_REPO,
    _is_github_url, _is_newer, _safe_extract_zip, _this_machine_tokens,
    _version_tuple
)
from .audioprep import (
    AudioPrep, MAX_RETRY_FRACTION, MAX_RETRY_SPANS, RetransJob,
    _AUDIO_CACHE_MAX_BYTES, _AUDIO_CACHE_MAX_FILES, _AUDIO_MIMETYPES,
    _AUDIO_PASSTHROUGH_EXTS, _AUDIO_REMUX_EXTS, _audio_cache_dir,
    _audio_cache_key, _budgeted_spans, _extract_audio_m4a_pyav,
    _remux_audio_pyav, _source_audio_codec, _sweep_audio_cache,
    auto_retranscribe, flagged_spans, retranscribe_slice
)
from .review import ReviewSession, autosave_restore_info, open_transcript_info
from .server import WebBackend, _PICK_SCRIPT, _webdist_dir
from .cli import (
    _ABOUT_MENU_TARGET, _attach_drop_handler, _bind_or_exit,
    _hook_about_menu, _parse_args, _require_web_stack, main, main_serve,
    main_web
)


# Spelt out so the re-exports above read as the package's public surface
# rather than as a wall of unused imports.
__all__ = [
    "version", "errors", "palettes", "speakers", "platformutil",
    "paragraphs", "live", "writers", "readers", "engines", "diarize",
    "worker", "workerproc", "transcript", "settings", "broker", "runctl",
    "models", "updates", "audioprep", "review", "server", "cli",
    "ABOUT_TEXT", "__version__", "ApiFail", "DiarizationUnavailable",
    "TranscriptParseError", "_CancelledByUser", "_EngineNotAvailable",
    "_ACTIVE_THEME", "_PALETTES", "_palette", "_resolve_theme",
    "MAX_SPEAKERS", "SPEAKER_SLOTS", "UNATTRIBUTED_LABEL",
    "_export_speaker_colour", "ANNOTATE_MODE", "_ANNOTATION_FIELDS",
    "_AUDIO_EXTENSIONS", "_MIGRATIONS", "_RECENT_MAX", "_REVEAL_LABEL",
    "_SCHEMA", "_annotations_file", "_annotations_load",
    "_annotations_lock", "_annotations_save", "_autosave_clear",
    "_autosave_file", "_autosave_load", "_autosave_save", "_config_dir",
    "_find_readme", "_guess_audio_for_transcript", "_install_crash_logging",
    "_log", "_log_dir", "_log_file_path", "_migrate_recent_0_to_1",
    "_next_revision_path", "_open_path", "_open_url", "_read_versioned",
    "_recent_add", "_recent_file", "_recent_load", "_recent_save",
    "_reveal_path", "_settings_file", "_settings_load", "_settings_save",
    "_terms_add", "_terms_all", "_terms_folder_key", "_terms_load",
    "_terms_path", "_terms_save", "_unwrap_versioned", "_write_json_atomic",
    "_write_versioned", "annotate_enabled", "annotation_add",
    "annotation_delete", "annotations_clear", "set_annotate_mode",
    "AUDIO_SAMPLE_RATE", "ParagraphStream", "SHORT_RESPONSES",
    "_GAP_SAMPLE_SEGMENTS", "_INTERRUPTION_ENDINGS",
    "_NON_AUDIO_SIGNATURES", "_PARAGRAPH_SECONDS_CAP",
    "_SOFT_BREAK_FRACTION", "_SOFT_BREAK_MIN_SECONDS", "_disclaimer_text",
    "_ffprobe_duration", "_format_duration", "_gaps_are_informative",
    "_have_pyav", "_is_sentence_continuation", "_is_short_response",
    "_leading_acknowledgment", "_pyav_duration", "_refine_segment_times",
    "_should_break", "decode_audio_16k", "describe_non_audio_file",
    "format_timestamp", "get_audio_duration", "paragraphify", "render",
    "LiveTranscript", "no_audio_marker", "segment_is_phantom",
    "DOC_TEMPLATES", "DOCX_FONT",
    "DOCX_FONT_PT", "DOCX_STYLE_DISCLAIMER", "DOCX_STYLE_FOOTER",
    "DOCX_STYLE_SPEAKER", "DOCX_STYLE_TEXT", "DOCX_STYLE_TITLE",
    "_docx_build_styles", "_docx_fix_font", "_docx_prune_unused_styles",
    "_docx_shade_style", "_format_srt_time", "_format_vtt_time",
    "_reviewed_result", "_seg_field", "_write_docx", "_write_extra_formats",
    "_write_json_result", "_write_pdf", "_write_srt", "_write_tsv",
    "_write_vtt", "write_paragraphs_to_file", "SEARCH_FILE_LIMIT",
    "SEARCH_HITS_PER_FILE", "_DISCLAIMER_RE", "_TRANSCRIPT_GLOBS",
    "_TS_PREFIX_RE", "_infer_paragraph_end_times", "_parse_docx_transcript",
    "_parse_timestamp_prefix", "_parse_txt_transcript",
    "_search_candidates", "read_paragraphs_from_file", "search_transcripts",
    "ENGINE_AUTO_NAME", "LANGUAGES", "MODEL_TIERS", "WHISPER_MODELS",
    "PRESETS", "PRESET_KEYS", "matching_preset", "preset", "settings_for",
    "_AVAILABLE_ENGINES", "_MLX_REPO_MAP", "_MODEL_ALIASES",
    "_canonical_model", "_detect_engines", "_macos_supports_mlx",
    "_mlx_repo_for", "_redetect_engines", "available_engines",
    "resolve_engine_key", "DIARIZE_DEFAULT_VOICE_MODEL",
    "DIARIZE_VOICE_MODELS", "_DIARIZE_CLUSTER_THRESHOLD",
    "_DIARIZE_MIN_COVERAGE", "_DIARIZE_MIN_MAJORITY",
    "_DIARIZE_SEGMENTATION", "_diarize_models_dir", "_download_one_model",
    "_run_diarization", "_voice_model_spec", "diarize_models_ready",
    "ensure_diarize_models", "label_paragraphs", "HALLUCINATION_COVERAGE",
    "HALLUCINATION_LOWCONF_FRACTION", "HALLUCINATION_LOWCONF_MINWORDS",
    "HALLUCINATION_LOWCONF_PROB", "HALLUCINATION_MIN_REPEATS",
    "SILENCE_MIN_SPAN_SECS", "SILENCE_RMS", "SILENCE_WINDOW_SECS",
    "_DownloadMonitor", "_MODEL_APPROX_MB", "_ProgressWriter",
    "_QueueWriter", "_SEGMENT_LINE_RE", "_SENTENCE_SPLIT_RE",
    "_TERM_STOPWORDS", "_TERM_TITLES", "_TERM_WORD_RE", "_WORD_RE",
    "_capitalised", "_drop_segments_in_silence", "_extract_word_conf",
    "_flush_run", "_has_repetition_loop", "_humanize_bytes",
    "_is_fully_periodic", "_looks_like_same_term", "_mostly_low_confidence",
    "_normalise_words", "_paragraphs_with_speakers", "_run_faster_whisper",
    "_run_mlx_whisper", "_run_openai_whisper", "_splice_no_audio_markers",
    "_term_key", "detect_hallucinations", "detect_silences",
    "extract_terms", "transcribe_worker", "CANCEL_GRACE_SECONDS",
    "SILENT_DEATH_SECONDS", "TERMINATE_GRACE_SECONDS", "_CHILD_FINISHED",
    "_CRASH_ADVICE", "_PipeQueue", "_SIGNAL_NOTES", "_TERMINAL",
    "_WINDOWS_FAULTS", "_child_main", "_describe_exit", "_signal_notes",
    "_stop", "default_worker_fn", "engine_runs_in_process",
    "run_worker_subprocess", "TranscriptModel", "_resolve_word_timestamps",
    "build_worker_params", "compose_initial_prompt", "_SETTINGS_BOOL_KEYS",
    "_SETTINGS_NUMBER_KEYS", "_settings_choices", "current_settings",
    "default_settings", "validate_settings", "EventBroker", "_sse_format",
    "RUN_PHASES", "RunController", "_MEDIA_EXTENSIONS", "_NO_ENGINE_HINT",
    "ensure_output_is_safe", "ModelController", "ModelStore",
    "_INSTALLABLE_ENGINES", "_WHISPER_PT_FALLBACK", "_dedupe_alias_models",
    "_download_status_text", "_engine_install_args",
    "_engine_uninstall_pkgs", "_faster_repo_for", "_hf_cache_dir",
    "_hf_delete_repo", "_hf_repo_sizes", "_pip_stream", "_prefetch_model",
    "_repo_belongs_to_engine", "_run_engine_op", "_uninstall_model",
    "_whisper_cache_dir", "_whisper_pt_filename", "UpdateChecker",
    "_ALL_ARCH_TOKENS", "_ARCH_TOKENS", "_ASSET_PREFERENCE",
    "_PLATFORM_INSTALLER", "_UPDATE_API", "_UPDATE_RELEASES_URL",
    "_UPDATE_REPO", "_is_github_url", "_is_newer", "_safe_extract_zip",
    "_this_machine_tokens", "_version_tuple", "AudioPrep",
    "MAX_RETRY_FRACTION", "MAX_RETRY_SPANS", "RetransJob",
    "_AUDIO_CACHE_MAX_BYTES", "_AUDIO_CACHE_MAX_FILES", "_AUDIO_MIMETYPES",
    "_AUDIO_PASSTHROUGH_EXTS", "_AUDIO_REMUX_EXTS", "_audio_cache_dir",
    "_audio_cache_key", "_budgeted_spans", "_extract_audio_m4a_pyav",
    "_remux_audio_pyav", "_source_audio_codec", "_sweep_audio_cache",
    "auto_retranscribe", "flagged_spans", "retranscribe_slice",
    "ReviewSession", "autosave_restore_info", "open_transcript_info",
    "WebBackend", "_PICK_SCRIPT", "_webdist_dir", "_ABOUT_MENU_TARGET",
    "_attach_drop_handler", "_bind_or_exit", "_hook_about_menu",
    "_parse_args", "_require_web_stack", "main", "main_serve", "main_web"
]

