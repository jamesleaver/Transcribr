"""The settings contract: defaults, validation, and persistence."""

from .platformutil import _settings_load
from .engines import (
    ALL_MODELS, ENGINE_AUTO_NAME, LANGUAGES, _canonical_model,
    available_engines
)
from .diarize import (
    DIARIZE_DEFAULT_VOICE_MODEL, DIARIZE_VOICE_MODELS,
    _DIARIZE_CLUSTER_THRESHOLD
)


#
# The web front-end shares settings.json with the Tk UI: same keys,
# same value conventions (engine and language are stored as display
# names, exactly as the Tk comboboxes hold them). validate_settings()
# mirrors _apply_settings(): unknown keys and junk values are dropped
# and missing ones fall back to the defaults, so a stale or corrupt
# file can't wedge the app.

# ("word_timestamps", "review" and "highlight_confidence" were stored
# settings until 0.9.0: word timings are now always recorded, single
# runs always open the review pane, and confidence shading is the
# review pane's own toggle. Stale keys in settings.json are dropped.)
_SETTINGS_BOOL_KEYS = (
    "show_timestamp", "condition_on_previous_text",
    "extra_json", "extra_srt", "extra_vtt",
    "extra_tsv", "show_details",
    "diarize", "show_all_models", "show_prompt", "show_diarize",
    "check_updates", "auto_retranscribe", "prime_with_terms",
    "voice_breaks",
    "colour_speakers",
    "offer_indistinct",
)

_SETTINGS_NUMBER_KEYS = (
    "gap", "temperature", "beam_size", "best_of",
    "compression_ratio_threshold", "logprob_threshold",
    "no_speech_threshold", "num_speakers", "diarize_threshold",
)


def _settings_choices():
    """Allowed values for every enumerated setting, computed live so
    the engine list reflects what is actually installed."""
    return {
        "engine": [ENGINE_AUTO_NAME] + [name for _, name in
                                        available_engines()],
        "model": list(ALL_MODELS),
        "diarize_model": [m["id"] for m in DIARIZE_VOICE_MODELS],
        "language": [n for n, _ in LANGUAGES],
        "task": ["transcribe", "translate"],
        "output_format": ["txt", "docx", "pdf"],
        "doc_template": ["plain", "court", "erisp"],
        "theme": ["auto", "light", "dark"],
        "word_timestamps": ["auto", "on", "off"],
    }


def default_settings():
    """The out-of-the-box settings - the same values the Tk widgets
    start with."""
    return {
        "engine": ENGINE_AUTO_NAME,
        "model": "large-v3-turbo",
        "language": "English",
        "task": "transcribe",
        "output_format": "docx",
        # How a Word document is laid out: "plain", "court" (numbered
        # lines, double spaced, running header) or "erisp".
        "doc_template": "plain",
        # Offer [indistinct] for passages the engine was unsure of. Only
        # ever offered; nothing is replaced without the reviewer.
        "offer_indistinct": True,
        "title": "",
        "prompt": "",
        # 1.0 s, not 1.5: it catches more speaker changes, with
        # monologues barely broken up any more.
        "gap": 1.0,
        "show_timestamp": True,
        # Off by default: a transcript that goes on a court file or to
        # the other side should look like every other transcript unless
        # its author decided otherwise.
        "colour_speakers": False,
        "temperature": 0.0,
        "beam_size": 5,
        "best_of": 5,
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        # Off: each 30-second window is decoded on its own. Feeding it
        # the text before it let one bad window poison the next - it
        # caused repetition loops and unpunctuated transcripts, and made
        # transcripts less accurate overall.
        "condition_on_previous_text": False,
        "extra_json": False,
        "extra_srt": False,
        "extra_vtt": False,
        "extra_tsv": False,
        "theme": "auto",
        "show_details": False,
        "diarize": False,
        # Two, not 0 ("work it out"): detection is far more reliable when
        # told the count; left to count for itself it can hear dozens of
        # speakers where there are a few. Two is the questioner-and-
        # witness case.
        "num_speakers": 2,
        "diarize_model": DIARIZE_DEFAULT_VOICE_MODEL,
        "diarize_threshold": _DIARIZE_CLUSTER_THRESHOLD,
        "show_all_models": False,
        # The field is shown: it is the most direct accuracy control in
        # the app and was hidden behind a setting most people never
        # found. Showing it costs nothing - an empty field primes
        # nothing.
        "show_prompt": True,
        # Word-level timing & confidence highlighting: "auto" (on except
        # on mlx, where it is slow), "on", or "off".
        "word_timestamps": "auto",
        # The experimental speaker-detection card stays hidden until
        # enabled from the Settings page.
        "show_diarize": False,
        # Ask GitHub once per launch whether a newer release exists.
        # It is the app's only unprompted network call, so it is a
        # setting rather than a given.
        "check_updates": True,
        # After a run, re-run the sections that look like engine
        # hallucination and keep whichever result the same check no
        # longer objects to.
        "auto_retranscribe": True,
        # Feed confirmed names to the engine before a run. Off by
        # default: priming fixes the names it is given and can invent
        # them where they were never said, which is the wrong trade for
        # a transcript used as evidence. Opt in knowingly.
        "prime_with_terms": False,
        # Start a new paragraph where the voice's pitch jumps after a
        # finished sentence. It catches more speaker changes, at the cost
        # of an occasional extra paragraph when one person is speaking.
        "voice_breaks": True,
    }


def validate_settings(raw, base=None):
    """Merge `raw` (a loaded settings.json, or a PUT body) over `base`
    (the defaults when None), accepting only known keys with plausible
    values. Passing the currently stored settings as `base` makes a
    partial update non-destructive."""
    import math

    merged = default_settings()
    if base:
        merged.update(base)
    if not isinstance(raw, dict):
        return merged
    raw = dict(raw)
    # One-time migration: the old single "Description of file" field was
    # both the Whisper prompt and the document title. They are separate
    # now. A pre-split settings.json has no "title" key - move its text
    # into the title (the benign use) and clear the prompt, so upgrading
    # doesn't keep silently priming the engine. Only at load time
    # (base is None), never on a partial PUT from the current UI.
    if base is None and "title" not in raw and raw.get("prompt"):
        raw["title"] = str(raw["prompt"])
        raw["prompt"] = ""
    for key, allowed in _settings_choices().items():
        v = raw.get(key)
        if key == "model" and isinstance(v, str):
            v = _canonical_model(v)   # legacy "large"/"turbo" -> canonical
        if isinstance(v, str) and v in allowed:
            merged[key] = v
    for key in ("title", "prompt"):
        v = raw.get(key)
        if isinstance(v, str):
            merged[key] = v
    for key in _SETTINGS_NUMBER_KEYS:
        v = raw.get(key)
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v)):
            merged[key] = v
    for key in _SETTINGS_BOOL_KEYS:
        v = raw.get(key)
        if isinstance(v, bool):
            merged[key] = v
    return merged


def current_settings():
    """Defaults overlaid with whatever settings.json currently holds."""
    return validate_settings(_settings_load())
