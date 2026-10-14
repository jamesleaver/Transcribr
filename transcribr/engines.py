"""Which Whisper engines and models exist, and which are installed."""

import sys


# The canonical model choices shown in the UI. "large" and "turbo" are
# omitted: they are just aliases of "large-v3" and "large-v3-turbo"
# (identical weights), so listing them was a confusing duplicate. The
# engines still accept the alias names, and _MODEL_ALIASES normalises any
# stored/legacy value to its canonical form.
WHISPER_MODELS = [
    "tiny.en", "base.en", "small.en", "medium.en",
    "tiny", "base", "small", "medium",
    "large-v1", "large-v2", "large-v3", "large-v3-turbo",
]

# Not Whisper: NVIDIA's Parakeet, run through sherpa-onnx
# (transcribr/parakeet.py). English only. Offered as a model so it can
# sit behind a tier; the worker sends it to its own runner whatever the
# engine setting says.
PARAKEET_MODEL = "parakeet-tdt-0.6b-v2"

# Every model a setting may name.
ALL_MODELS = WHISPER_MODELS + [PARAKEET_MODEL]

_MODEL_ALIASES = {"large": "large-v3", "turbo": "large-v3-turbo"}


def _canonical_model(name):
    """Map an alias model name to its canonical equivalent (identical
    weights), leaving anything else untouched."""
    return _MODEL_ALIASES.get(name, name)


# The three choices most users should pick between, presented instead of
# the raw twelve-model list (which stays available behind a "show all
# models" toggle, and in full detail in the Models view). `model_en` is
# substituted when the language is English - the .en variants are
# slightly more accurate there. Sizes are the one-time download on
# first use.
MODEL_TIERS = [
    # English: NVIDIA's Parakeet - quick, and it does not fall into
    # repetition loops. Other languages keep Whisper small, since
    # Parakeet knows English only.
    {"id": "draft", "label": "Quick draft",
     "model": "small", "model_en": PARAKEET_MODEL, "size": "~500 MB",
     "note": "Fastest by far. In English it uses NVIDIA's Parakeet, which "
             "does well on clear speech but less well in noisy rooms."},
    {"id": "standard", "label": "Standard", "recommended": True,
     "model": "large-v3-turbo", "model_en": "large-v3-turbo",
     "size": "~1.6 GB",
     "note": "Accurate and reasonably quick — right for most work."},
    {"id": "max", "label": "Maximum accuracy",
     "model": "large-v3", "model_en": "large-v3", "size": "~3 GB",
     "note": "Slowest. For difficult audio where every word matters."},
]


# ---- Engine detection ---------------------------------------------------
#
# Transcribr can drive three Whisper implementations. We probe each one's
# import at startup and only offer installed ones to the user. The engine
# key (e.g. "faster") is what flows through params; the display name is
# what appears in the dropdown.

def _macos_supports_mlx():
    """mlx (Apple's framework, which mlx-whisper depends on) refuses to
    import on macOS < 13.5. Return False on older versions so we don't
    offer the engine in the dropdown - find_spec sees the package but
    the runtime import would raise."""
    if sys.platform != "darwin":
        return False
    try:
        import platform
        if platform.machine() not in ("arm64", "aarch64"):
            return False        # mlx ships no x86_64 macOS wheels
        ver = platform.mac_ver()[0]
        if not ver:
            return False
        parts = ver.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor) >= (13, 5)
    except (ValueError, IndexError):
        return False


def _detect_engines():
    """Return a list of (key, display_name) for installed engines."""
    import importlib.util as _ilu
    engines = []
    if _ilu.find_spec("whisper") is not None:
        engines.append(("whisper", "OpenAI Whisper (reference)"))
    if _ilu.find_spec("faster_whisper") is not None:
        engines.append(("faster", "faster-whisper (CTranslate2)"))
    # mlx-whisper is Apple Silicon-only and needs macOS 13.5+.
    if (_macos_supports_mlx()
            and _ilu.find_spec("mlx_whisper") is not None):
        engines.append(("mlx", "mlx-whisper (Apple Silicon)"))
    return engines


# Which engines are importable right now. Held privately and reached
# through available_engines(), because the Models view can pip-install
# one mid-session: a module that had done `from ... import
# AVAILABLE_ENGINES` would hold the list as it stood at import time and
# never see the new engine.
_AVAILABLE_ENGINES = _detect_engines()


def available_engines():
    """[(key, display name)] for every engine importable right now."""
    return _AVAILABLE_ENGINES

# The "just do the right thing" engine choice, shown first in the
# dropdown and used as the default. It resolves at run time so the same
# settings file does the right thing when copied between machines.
ENGINE_AUTO_NAME = "Automatic (recommended)"


def resolve_engine_key(engine_name):
    """Map a settings engine display name to a runner key. The Automatic
    entry (and any stale/unknown name, e.g. after the engine it named
    was uninstalled from the Models view) resolves to the best installed
    engine: faster-whisper, then mlx-whisper, then the reference
    implementation.

    faster-whisper comes first for accuracy, not speed. mlx-whisper is
    quicker on Apple Silicon, but faster-whisper is the more accurate
    choice for difficult recordings, and mlx cannot be stopped mid-run.
    mlx stays a click away for anyone who wants the speed."""
    for key, name in available_engines():
        if name == engine_name:
            return key
    keys = [key for key, _ in available_engines()]
    for preferred in ("faster", "mlx", "whisper"):
        if preferred in keys:
            return preferred
    return keys[0] if keys else "whisper"


# mlx-whisper takes a HuggingFace repo path (since the runtime weights are
# converted to MLX format). Map our common short names to mlx-community
# repos. Fall back to a heuristic for anything not in the table.
_MLX_REPO_MAP = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v1": "mlx-community/whisper-large-v1-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def _mlx_repo_for(model_name):
    return _MLX_REPO_MAP.get(
        model_name, f"mlx-community/whisper-{model_name}-mlx")

# (Display name, code passed to whisper). None = auto-detect.
LANGUAGES = [
    ("Auto-detect", None),
    ("English", "en"),
    ("Chinese (Mandarin)", "zh"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Italian", "it"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Portuguese", "pt"),
    ("Russian", "ru"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
    ("Vietnamese", "vi"),
    ("Indonesian", "id"),
    ("Dutch", "nl"),
    ("Turkish", "tr"),
    ("Polish", "pl"),
]


def _redetect_engines():
    """Re-probe which engines are importable and refresh the module-level
    engine list behind available_engines() (read live by /api/meta and
    the controllers), so a just-installed engine appears without a
    restart."""
    import importlib
    importlib.invalidate_caches()
    global _AVAILABLE_ENGINES
    _AVAILABLE_ENGINES = _detect_engines()
    return _AVAILABLE_ENGINES
