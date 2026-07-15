#!/usr/bin/env python3
"""
Transcribr - GUI for transcribing audio/video files with Whisper,
grouping the result into paragraphs, and reviewing/labelling speakers.

(c) James Leaver, 2026. This software is experimental. Everything runs
locally on your computer: a Whisper engine (openai-whisper,
faster-whisper, or mlx-whisper) transcribes the audio, the result is
grouped into paragraphs, and a review pane lets you label speakers,
edit text, and listen back to the source audio before saving as .docx,
.pdf, or .txt. Several files can be queued and transcribed in one
unattended batch. When a particular model is run for the first time it
is downloaded and stored locally; 'medium.en' or 'large-v3-turbo' are
recommended. Use at your own risk.
Questions: jleaver@sgchambers.com.au.

Run with:
    python3 transcribr.py
"""

__version__ = "0.6.0"

ABOUT_TEXT = (
    f"Version {__version__}\n"
    "(c) James Leaver, 2026.\n\n"
    "Transcribr transcribes audio and video with Whisper, groups the "
    "result into paragraphs, and gives you a review pane to label "
    "speakers, edit text, and listen back to the source audio before "
    "saving as Word (.docx), PDF, or plain text. Several files can be "
    "queued and transcribed in one unattended batch. Everything runs "
    "locally on your computer - nothing is uploaded.\n\n"
    "When a particular model is run for the first time, that model will "
    "be downloaded to your computer and stored locally. The 'medium.en' "
    "or 'large-v3-turbo' models are recommended.\n\n"
    "This software is experimental. Use at your own risk.\n\n"
    "Questions: jleaver@sgchambers.com.au"
)

import contextlib
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# Drag-and-drop is provided by the optional 'tkinterdnd2' package.
# If it is not installed, drag-and-drop is silently disabled and the GUI
# still works normally via the Browse... button.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# Modern theming is provided by the optional 'sv-ttk' package (Sun Valley
# theme) with 'darkdetect' to follow the system light/dark appearance.
# Without them the app runs with the platform's stock ttk theme, and the
# light colour palette below still applies to the plain-tk widgets.
try:
    import sv_ttk
except ImportError:
    sv_ttk = None
try:
    import darkdetect
except ImportError:
    darkdetect = None


# =====================================================================
# Colour palettes (light / dark)
# =====================================================================
#
# ttk widgets are restyled wholesale by sv-ttk, but plain tk widgets (the
# transcript Text, the prompt/log text boxes, the batch Listbox, speaker
# badges, the drop zone) need explicit colours. Everything below reads
# the active palette via _palette() so a theme switch can re-skin live.

_PALETTES = {
    "light": {
        "text_bg": "#ffffff", "text_fg": "#111111", "insert": "#111111",
        "speaker_fg": "#222222", "timestamp_fg": "#777777",
        "selected_bg": "#d6d6d6", "editing_bg": "#ffcc80",
        "search_bg": "#ffe066",
        "conf_low": "#ffd2d2", "conf_med": "#ffe9c7",
        "badge_fg": "#111111",
        "speaker_colours": {
            "1": "#fff4d6",   # warm yellow
            "2": "#dfeeff",   # soft blue
            "3": "#e3f4d8",   # soft green
            "4": "#f9d9e7",   # soft pink
            "5": "#e7ddf6",   # lavender
            "6": "#ffe0c2",   # peach
            "7": "#d3f0ee",   # teal
            "8": "#f0e4c8",   # tan
            "9": "#d9e8c0",   # olive
        },
        "drop_bg": "#f5f6f8", "drop_hover": "#e8f0fe",
        "drop_border": "#9aa0a6", "drop_fg": "#5f6368",
    },
    "dark": {
        "text_bg": "#1e1e20", "text_fg": "#e6e6e6", "insert": "#e6e6e6",
        "speaker_fg": "#dddddd", "timestamp_fg": "#9a9a9a",
        "selected_bg": "#46484c", "editing_bg": "#6b4e1e",
        "search_bg": "#7a6a14",
        "conf_low": "#5c2b2b", "conf_med": "#5c4a26",
        "badge_fg": "#e6e6e6",
        "speaker_colours": {
            "1": "#4d4426",   # warm yellow, dimmed
            "2": "#283d52",   # soft blue, dimmed
            "3": "#2f4528",   # soft green, dimmed
            "4": "#4d2c3c",   # soft pink, dimmed
            "5": "#3b3050",   # lavender, dimmed
            "6": "#52391f",   # peach, dimmed
            "7": "#1f4441",   # teal, dimmed
            "8": "#46402a",   # tan, dimmed
            "9": "#3a4426",   # olive, dimmed
        },
        "drop_bg": "#2a2a2c", "drop_hover": "#1f3a5f",
        "drop_border": "#5f6368", "drop_fg": "#9aa0a6",
    },
}

# The currently-active palette key. Set by _apply_theme(); defaults to
# light so module-level consumers work before the GUI configures a theme.
_ACTIVE_THEME = "light"


def _resolve_theme(setting):
    """Map a theme setting ("auto"/"light"/"dark") to a palette key."""
    if setting in ("light", "dark"):
        return setting
    if darkdetect is not None:
        try:
            if (darkdetect.theme() or "").lower() == "dark":
                return "dark"
        except Exception:
            pass
    return "light"


def _palette():
    return _PALETTES[_ACTIVE_THEME]


def _apply_theme(setting):
    """Activate the palette for `setting` and, when sv-ttk is installed,
    restyle every ttk widget to match. Safe to call repeatedly."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = _resolve_theme(setting)
    if sv_ttk is not None:
        try:
            sv_ttk.set_theme(_ACTIVE_THEME)
        except Exception as e:
            _log(f"sv-ttk theme switch failed: {e}")


# =====================================================================
# Cross-platform helpers
# =====================================================================

if sys.platform == "darwin":
    _REVEAL_LABEL = "Reveal in Finder"
elif sys.platform == "win32":
    _REVEAL_LABEL = "Show in Explorer"
else:
    _REVEAL_LABEL = "Show in Folder"


def _open_path(path):
    """Open a file with the OS's default handler."""
    path = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        # Best-effort; failing to open shouldn't break anything else.
        pass


def _reveal_path(path):
    """Show the file in the OS's file manager, with the file selected."""
    path = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        elif sys.platform == "win32":
            # explorer /select, requires no space after the comma; it also
            # exits non-zero even on success, so we don't check the return.
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        else:
            # Linux: open the parent folder; selecting a specific file isn't
            # universally supported across file managers.
            subprocess.run(["xdg-open", str(Path(path).parent)], check=False)
    except Exception:
        pass


def _find_readme():
    """Return a path to README.md / README.txt if one exists near the script."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "README.md",
        here / "README.txt",
        here.parent / "README.md",
        here.parent / "README.txt",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _log_dir():
    """Return a writable per-user directory for log files."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Transcribr"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA",
                                   Path.home() / "AppData" / "Local"))
        base = base / "Transcribr" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME",
                                   Path.home() / ".local" / "state"))
        base = base / "Transcribr"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _log_file_path():
    return _log_dir() / "transcribr.log"


def _log(msg, *, exc_info=None):
    """Append a timestamped line to the log file. Best-effort; never raises."""
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(_log_file_path(), "a", encoding="utf-8") as fh:
            fh.write(line)
            if exc_info is not None:
                traceback.print_exception(*exc_info, file=fh)
                fh.write("\n")
    except Exception:
        pass


def _install_crash_logging(root=None):
    """Route uncaught Python and Tk callback exceptions to the log file."""
    def py_hook(exc_type, exc, tb):
        _log("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = py_hook
    if root is not None:
        def tk_hook(exc_type, exc, tb):
            _log("TK CALLBACK EXCEPTION", exc_info=(exc_type, exc, tb))
            traceback.print_exception(exc_type, exc, tb)
        root.report_callback_exception = tk_hook


def _next_revision_path(original):
    """Return a sibling path with a .revN suffix that doesn't yet exist.

    e.g. /a/b.docx -> /a/b.rev1.docx, then .rev2.docx, etc. Strips an
    existing .revN suffix on the input so revisions of revisions don't
    pile up nested suffixes."""
    p = Path(original)
    ext = p.suffix
    stem = p.stem
    m = re.match(r"^(.*)\.rev\d+$", stem)
    if m:
        stem = m.group(1)
    n = 1
    while True:
        candidate = p.with_name(f"{stem}.rev{n}{ext}")
        if not candidate.exists():
            return candidate
        n += 1


_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
                     ".opus", ".mp4", ".mov", ".mkv", ".avi", ".webm")


def _guess_audio_for_transcript(transcript_path):
    """Best-effort: find the source audio/video file next to a saved
    transcript, so playback works when re-opening it for review.

    Transcripts are written as <media stem>.transcript.<ext> (possibly
    with a .revN inserted), so we strip those suffixes and probe the
    known media extensions. Returns a str path or None."""
    p = Path(transcript_path)
    stem = p.stem  # e.g. "interview.transcript" or "interview.transcript.rev2"
    m = re.match(r"^(.*)\.rev\d+$", stem)
    if m:
        stem = m.group(1)
    if stem.endswith(".transcript"):
        stem = stem[: -len(".transcript")]
    for ext in _AUDIO_EXTENSIONS:
        candidate = p.with_name(stem + ext)
        if candidate.exists():
            return str(candidate)
    return None


# ---- Recent transcripts persistence -------------------------------------

_RECENT_MAX = 10


def _config_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Transcribr"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA",
                                   Path.home() / "AppData" / "Roaming"))
        base = base / "Transcribr"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME",
                                   Path.home() / ".config"))
        base = base / "Transcribr"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _recent_file():
    return _config_dir() / "recent.json"


def _recent_load():
    """Return the list of recent transcript paths, most-recent first.
    Best-effort; returns [] on any read/parse error."""
    try:
        import json
        path = _recent_file()
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(p) for p in data if isinstance(p, str)]
    except Exception:
        pass
    return []


def _recent_save(paths):
    try:
        import json
        with open(_recent_file(), "w", encoding="utf-8") as fh:
            json.dump(list(paths)[:_RECENT_MAX], fh, indent=2)
    except Exception as e:
        _log(f"Could not write recent.json: {e}")


def _recent_add(path):
    """Move `path` to the front of the recent list (deduping). No-op on error."""
    try:
        path = str(Path(path).resolve())
    except Exception:
        path = str(path)
    items = _recent_load()
    items = [p for p in items if p != path]
    items.insert(0, path)
    _recent_save(items)


def _settings_file():
    return _config_dir() / "settings.json"


def _settings_load():
    """Return the saved UI settings dict, or {} on any read/parse error."""
    try:
        import json
        path = _settings_file()
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _settings_save(settings):
    """Persist the UI settings dict. Best-effort; logs on failure."""
    try:
        import json
        with open(_settings_file(), "w", encoding="utf-8") as fh:
            json.dump(dict(settings), fh, indent=2)
    except Exception as e:
        _log(f"Could not write settings.json: {e}")


# ---- Review auto-save (crash recovery) -----------------------------------
#
# While the review pane is open, the labelled-but-unsaved state is
# periodically written here. It is deleted on any clean exit from review;
# if it survives to the next launch, the app offers to restore it.

def _autosave_file():
    return _config_dir() / "autosave.json"


def _autosave_load():
    """Return the saved review session dict, or None."""
    try:
        import json
        path = _autosave_file()
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("paragraphs"):
            return data
    except Exception:
        pass
    return None


def _autosave_save(data):
    try:
        import json
        with open(_autosave_file(), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception as e:
        _log(f"Could not write autosave.json: {e}")


def _autosave_clear():
    try:
        _autosave_file().unlink(missing_ok=True)
    except Exception:
        pass


def _set_window_icon(root):
    """Apply icon.ico (Windows) or icon.png (Linux) if it exists alongside the
    script. macOS uses the .app bundle's icon and ignores window-level icons."""
    here = Path(__file__).resolve().parent
    try:
        if sys.platform == "win32":
            ico = here / "icon.ico"
            if ico.exists():
                root.iconbitmap(str(ico))
        elif sys.platform.startswith("linux"):
            png = here / "icon.png"
            if png.exists():
                root.iconphoto(True, tk.PhotoImage(file=str(png)))
    except tk.TclError:
        pass


# =====================================================================
# Paragraphify (identical to the standalone script)
# =====================================================================

# Sentinel speaker label used in saved transcripts to mark a paragraph
# that explicitly has no speaker assigned, so that loading the file back
# distinguishes "unlabelled by intent" from "continuing the previous
# speaker". Only emitted on a transition from a named speaker to an
# unattributed paragraph; runs of unattributed paragraphs at the start of
# a transcript don't need the marker.
UNATTRIBUTED_LABEL = "[Unattributed]"

# Which review pane implementation to use:
#   "rows" - the original card-style row-per-paragraph pane (with
#            virtualization). Cleaner visual style, but creating widget
#            trees on fast scroll is expensive even with virtualization.
#   "text" - a single tk.Text widget for the whole transcript. Tk's Text
#            handles huge documents natively, so scrolling is instant.
#            Visual style is a continuous document instead of cards.
REVIEW_PANE_STYLE = os.environ.get("TRANSCRIBR_REVIEW", "text").lower()


SHORT_RESPONSES = {
    "yes", "yeah", "yep", "yup",
    "no", "nope", "nah",
    "okay", "ok", "alright", "all right",
    "mm", "mhm", "mmhm", "uh huh", "uh-huh",
    "right", "sure", "correct", "true", "exactly",
    "thanks", "thank you",
}


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"[{h:d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def _format_duration(seconds: float) -> str:
    """Human-readable duration: '12s', '3m 04s', '1h 23m'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


def get_audio_duration(path):
    """Return the duration of an audio/video file in seconds, or None on failure.

    Uses ffprobe (shipped with ffmpeg, which is already a hard requirement
    for whisper). We pass quiet flags so the subprocess doesn't pollute
    stdout/stderr on the GUI side.

    On Windows we hide the console window that subprocess.run would
    otherwise briefly flash for the ffprobe process.
    """
    import subprocess
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, timeout=15,
            creationflags=creationflags,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _is_short_response(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s'-]", "", text).strip().lower()
    if not cleaned:
        return False
    if cleaned in SHORT_RESPONSES:
        return True
    words = cleaned.split()
    return len(words) <= 3 and all(w in SHORT_RESPONSES for w in words)


def _should_break(prev, curr, gap_threshold: float) -> bool:
    if prev is None:
        return True
    _, prev_end, prev_text = prev
    curr_start, _, curr_text = curr
    prev_stripped = prev_text.rstrip()

    # Hard silence beyond the user's pause threshold.
    if curr_start - prev_end >= gap_threshold:
        return True
    # Sentence-ending punctuation at a segment boundary is a paragraph
    # break. Whisper decides where to end its segments based on
    # silence + linguistic cues, so when one of its segments ends with
    # a full stop / exclamation / question mark, that's a natural
    # paragraph boundary - we should honour it rather than join the
    # next segment on. (Previously only "?" did this, which left long
    # statement-style runs collapsed into a single paragraph.)
    if prev_stripped.endswith((".", "!", "?")):
        return True
    # Short conversational responses (yes/no/etc.) on either side.
    if _is_short_response(prev_text) or _is_short_response(curr_text):
        return True
    return False


# Safety cap: even if no break signal fires, a paragraph shouldn't grow
# beyond this many seconds of audio. Without the cap, an uninterrupted
# monologue can collapse into a single unreadable paragraph in the
# review pane.
_PARAGRAPH_SECONDS_CAP = 60.0


def paragraphify(segments, gap_threshold: float):
    paragraphs, current, prev = [], [], None
    para_start = None
    for seg in segments:
        if current:
            forced_by_cap = (
                para_start is not None
                and seg[1] - para_start >= _PARAGRAPH_SECONDS_CAP)
            if forced_by_cap or _should_break(prev, seg, gap_threshold):
                paragraphs.append(current)
                current = []
                para_start = None
        if para_start is None:
            para_start = seg[0]
        current.append(seg)
        prev = seg
    if current:
        paragraphs.append(current)
    return paragraphs


def render(paragraphs, *, show_timestamp=True, title=None, speakers=None) -> str:
    """Render paragraphs to plain text.

    `speakers`, if given, is a list parallel to `paragraphs` where each
    entry is either a speaker label string (e.g. "CONSTABLE MACKLEBUM")
    or None for paragraphs with no assigned speaker. We only emit the
    speaker label when it differs from the previous paragraph's, which
    matches conventional transcript style.
    """
    out_lines = []
    if title:
        # Plain text has no bold; we just put the title at the top.
        # The "\n\n".join below produces a blank line between this title
        # and the first paragraph.
        out_lines.append(title)

    last_speaker = None
    for i, para in enumerate(paragraphs):
        start = para[0][0]
        body = " ".join(seg[2] for seg in para)
        speaker = speakers[i] if speakers and i < len(speakers) else None
        block_lines = []
        if speaker and speaker != last_speaker:
            block_lines.append(f"{speaker}:")
        elif speaker is None and last_speaker is not None:
            # Transition from a named speaker to an unattributed paragraph.
            # Emit an explicit marker so re-parsing this file doesn't carry
            # the previous speaker over.
            block_lines.append(f"{UNATTRIBUTED_LABEL}:")
        if show_timestamp:
            block_lines.append(f"{format_timestamp(start)}  {body}")
        else:
            block_lines.append(body)
        out_lines.append("\n".join(block_lines))
        last_speaker = speaker

    return "\n\n".join(out_lines) + "\n"


def write_paragraphs_to_file(paragraphs, out_path, *, show_timestamp=True,
                              title=None, output_format="txt", speakers=None):
    """Single entry point for writing transcript output.

    Centralises the txt-vs-docx-vs-pdf switch so the worker (direct-write
    path) and the GUI (review-screen path) can both call the same function.

    Raises ImportError with a friendly message if .docx or .pdf is
    requested but the needed package isn't installed.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "docx":
        try:
            _write_docx(paragraphs, out_path,
                        show_timestamp=show_timestamp, title=title,
                        speakers=speakers)
        except ImportError:
            raise ImportError(
                "Cannot write .docx: the 'python-docx' package is not "
                "installed. Install it with:\n  pip install python-docx\n"
                "Or pick the .txt format instead.")
    elif output_format == "pdf":
        try:
            _write_pdf(paragraphs, out_path,
                       show_timestamp=show_timestamp, title=title,
                       speakers=speakers)
        except ImportError:
            raise ImportError(
                "Cannot write .pdf: the 'reportlab' package is not "
                "installed. Install it with:\n  pip install reportlab\n"
                "Or pick the .txt or .docx format instead.")
    else:
        out_path.write_text(
            render(paragraphs, show_timestamp=show_timestamp,
                   title=title, speakers=speakers),
            encoding="utf-8",
        )


# =====================================================================
# Reading transcripts back in (the inverse of writing)
# =====================================================================

# Matches "[MM:SS]" or "[H:MM:SS]" at the start of a line (paragraph body).
_TS_PREFIX_RE = re.compile(r"^\s*\[(\d+:)?(\d+):(\d+)\]\s*")

# Matches a Transcribr disclaimer line. We strip these so they don't
# come back as paragraphs.
_DISCLAIMER_RE = re.compile(
    r"^Transcribed using Transcribr",
    re.IGNORECASE,
)


def _parse_timestamp_prefix(line):
    """Return (start_seconds, body_text) if `line` begins with [MM:SS] or
    [H:MM:SS], else (None, line). Used by both .txt and .docx parsers."""
    m = _TS_PREFIX_RE.match(line)
    if not m:
        return None, line
    h = int(m.group(1)[:-1]) if m.group(1) else 0
    mm = int(m.group(2))
    ss = int(m.group(3))
    return h * 3600 + mm * 60 + ss, line[m.end():]


class TranscriptParseError(Exception):
    """Raised when a file can't be parsed as a Transcribr transcript."""


def read_paragraphs_from_file(path):
    """Read a Transcribr-written .txt or .docx and return a dict with:
        paragraphs    - list of paragraphs in the format used by the
                        review pane (each paragraph is a list of
                        (start, end, text) tuples; we synthesise one
                        segment per paragraph since the original
                        segmentation isn't recoverable)
        speakers      - list of speaker name strings (or None) parallel
                        to paragraphs
        title         - the title at the top of the document, or None
        show_timestamp - True if the file contained timestamps, False
                        if not (informs how it should be re-rendered)

    Raises TranscriptParseError on any failure with a message suitable
    for showing the user.
    """
    path = Path(path)
    if not path.exists():
        raise TranscriptParseError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".txt":
        parsed = _parse_txt_transcript(path)
    elif suffix == ".docx":
        parsed = _parse_docx_transcript(path)
    else:
        raise TranscriptParseError(
            f"Unsupported file extension: {suffix}. Only .txt and .docx are supported."
        )
    _infer_paragraph_end_times(parsed["paragraphs"])
    return parsed


def _infer_paragraph_end_times(paragraphs):
    """Saved transcripts only record each paragraph's start time, so the
    parsers synthesise a placeholder 1-second span. Stretch each
    paragraph's end out to the next paragraph's start so the span covers
    the actual speech - audio playback depends on this. The last
    paragraph's true end is unknowable from the file; it keeps the
    placeholder span and playback treats it as open-ended."""
    for i in range(len(paragraphs) - 1):
        para = paragraphs[i]
        nxt = paragraphs[i + 1]
        if not para or not nxt:
            continue
        next_start = nxt[0][0]
        start, end, text = para[-1]
        if next_start > end:
            para[-1] = (start, next_start, text)


def _parse_txt_transcript(path):
    """Parse a .txt file written by render(). Format:

        <title (optional)>

        SPEAKER NAME:
        [MM:SS]  body text

        [MM:SS]  body text from same speaker (no label since unchanged)

        OTHER SPEAKER:
        [MM:SS]  ...

        Transcribed using Transcribr ... (disclaimer line - skipped)

    Blocks are separated by blank lines. We tolerate transcripts that
    omit timestamps (text-only output) and transcripts that omit
    speaker labels (just text and timestamps)."""
    text = path.read_text(encoding="utf-8")
    # Split into blocks at blank lines. Strip trailing whitespace per block
    # but preserve internal newlines (a block is "SPEAKER:\n[MM:SS]  body").
    blocks = [b.strip("\n") for b in re.split(r"\n[ \t]*\n", text) if b.strip()]
    if not blocks:
        raise TranscriptParseError("File is empty.")

    title = None
    paragraphs = []
    speakers = []
    current_speaker = None
    saw_timestamp = False  # tracks whether we ever saw [MM:SS]

    # First block could be the title. A title looks like a single line
    # that isn't a speaker label and doesn't start with a timestamp.
    first = blocks[0]
    first_lines = first.splitlines()
    looks_like_speaker = (len(first_lines) >= 1
                          and first_lines[0].rstrip().endswith(":")
                          and not _TS_PREFIX_RE.match(first_lines[0]))
    has_timestamp = bool(_TS_PREFIX_RE.match(first_lines[0]))
    if not looks_like_speaker and not has_timestamp and len(first_lines) <= 3:
        # Treat as title and consume.
        title = first
        blocks = blocks[1:]

    for block in blocks:
        # Skip disclaimer
        if _DISCLAIMER_RE.match(block):
            continue
        lines = block.splitlines()
        # Speaker label is the first line iff it ends with ":" and isn't
        # a timestamp.
        if lines and lines[0].rstrip().endswith(":") and not _TS_PREFIX_RE.match(lines[0]):
            label = lines[0].rstrip().rstrip(":").strip()
            # Recognise the explicit "no speaker" marker we emit on
            # transitions from a named speaker back to unattributed.
            current_speaker = None if label == UNATTRIBUTED_LABEL else label
            lines = lines[1:]
        if not lines:
            # Speaker line on its own with no body. Skip; we'll attach
            # the speaker to the next block's body.
            continue
        # The remaining lines are paragraph body. Look for timestamp prefix
        # on the first content line.
        first_content = lines[0]
        ts, after_ts = _parse_timestamp_prefix(first_content)
        if ts is not None:
            saw_timestamp = True
            start_t = ts
            lines[0] = after_ts.lstrip()
        else:
            start_t = 0.0
        body = " ".join(line.strip() for line in lines if line.strip())
        if not body:
            continue
        # Synthesise a single segment for this paragraph.
        paragraphs.append([(start_t, start_t + 1.0, body)])
        speakers.append(current_speaker)

    if not paragraphs:
        raise TranscriptParseError(
            "No transcript content found. The file may not be a "
            "Transcribr-produced transcript."
        )
    return {
        "paragraphs": paragraphs,
        "speakers": speakers,
        "title": title,
        "show_timestamp": saw_timestamp,
    }


def _parse_docx_transcript(path):
    """Parse a .docx file written by _write_docx().

    We rely on paragraph styles to identify what each paragraph is:
        - "Title" / first bold paragraph -> title
        - "SpeakerLabel" -> speaker line
        - "Transcript" -> body paragraph (timestamp + tab + body)
        - everything else -> ignored (disclaimer, blank spacers, etc.)

    Word's "Clear Formatting" or paste operations can lose the styles.
    For robustness, we also fall back to text-based heuristics if a
    paragraph has no recognised style: lines ending with ':' look like
    speakers, lines starting with [MM:SS] look like body."""
    try:
        from docx import Document
    except ImportError:
        raise TranscriptParseError(
            "Cannot read .docx files: the 'python-docx' package is not "
            "installed."
        )
    try:
        doc = Document(str(path))
    except Exception as e:
        raise TranscriptParseError(f"Could not open .docx file: {e}")

    title = None
    paragraphs = []
    speakers = []
    current_speaker = None
    saw_timestamp = False

    docx_paragraphs = list(doc.paragraphs)
    if not docx_paragraphs:
        raise TranscriptParseError("Document is empty.")

    # First paragraph is treated as the title if it's bold and doesn't
    # look like a speaker label or body line. We also accept the
    # explicit pattern we wrote: bold Courier New 12pt without a colon
    # at the end.
    first = docx_paragraphs[0]
    first_text = first.text.strip()
    if first_text:
        runs_bold = all(
            (r.bold or False) for r in first.runs if r.text.strip()
        ) if first.runs else False
        looks_speaker = first_text.endswith(":")
        has_ts = bool(_TS_PREFIX_RE.match(first_text))
        if runs_bold and not looks_speaker and not has_ts:
            title = first_text
            docx_paragraphs = docx_paragraphs[1:]

    for p in docx_paragraphs:
        text = p.text.strip()
        if not text:
            continue
        if _DISCLAIMER_RE.match(text):
            continue
        style_name = ""
        try:
            style_name = (p.style.name or "").strip()
        except Exception:
            pass

        # Speaker label: explicit style, or bold + ends with ':'.
        if style_name == "SpeakerLabel" or (
            text.endswith(":") and not _TS_PREFIX_RE.match(text)
        ):
            label = text.rstrip(":").strip()
            current_speaker = None if label == UNATTRIBUTED_LABEL else label
            continue

        # Body paragraph: explicit style, or first chars are timestamp.
        # In python-docx, tabs in run text appear as "\t" characters.
        body_text = text.replace("\t", " ")
        ts, after_ts = _parse_timestamp_prefix(body_text)
        if ts is not None:
            saw_timestamp = True
            start_t = ts
            body = after_ts.strip()
        else:
            start_t = 0.0
            body = body_text.strip()
        if not body:
            continue
        paragraphs.append([(start_t, start_t + 1.0, body)])
        speakers.append(current_speaker)

    if not paragraphs:
        raise TranscriptParseError(
            "No transcript content found. The file may not be a "
            "Transcribr-produced transcript."
        )
    return {
        "paragraphs": paragraphs,
        "speakers": speakers,
        "title": title,
        "show_timestamp": saw_timestamp,
    }


# =====================================================================
# Whisper option metadata
# =====================================================================

WHISPER_MODELS = [
    "tiny.en", "base.en", "small.en", "medium.en",
    "tiny", "base", "small", "medium",
    "large-v1", "large-v2", "large-v3", "large",
    "turbo", "large-v3-turbo",
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


AVAILABLE_ENGINES = _detect_engines()


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


class _EngineNotAvailable(Exception):
    """Raised when the worker can't import the requested engine."""
    pass

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


# =====================================================================
# Background worker
# =====================================================================

class _QueueWriter:
    """File-like that forwards writes to a queue as ('log', text) messages."""
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(("log", text))

    def flush(self):
        pass


class _CancelledByUser(Exception):
    """Raised inside whisper to abort transcription mid-run."""
    pass


def _extract_word_conf(result):
    """Flatten per-word (start, end, word, probability) tuples from a
    Whisper-style result dict. Returns [] when no word-level data is present
    (i.e. word timestamps weren't requested). Tolerant of missing keys so it
    works across the openai-whisper, faster-whisper and mlx-whisper result
    shapes (all of which expose segment['words'] as dicts after we
    normalise faster-whisper's objects)."""
    if not result:
        return []
    out = []
    for seg in result.get("segments", []):
        if not isinstance(seg, dict):
            continue
        for w in seg.get("words") or []:
            try:
                start = float(w.get("start"))
                end = float(w.get("end"))
            except (TypeError, ValueError, AttributeError):
                continue
            text = (w.get("word") or "") if isinstance(w, dict) else ""
            prob = w.get("probability") if isinstance(w, dict) else None
            try:
                prob = float(prob) if prob is not None else None
            except (TypeError, ValueError):
                prob = None
            if text.strip():
                out.append((start, end, text, prob))
    return out


def transcribe_worker(params, q, cancel_event):
    """Background-thread entry point. Dispatches to the chosen engine's
    runner, then handles the common post-processing: paragraphify, review-
    or-direct-save, extra output formats."""
    engine = params.get("engine", "whisper")
    runner = {
        "whisper": _run_openai_whisper,
        "faster": _run_faster_whisper,
        "mlx": _run_mlx_whisper,
    }.get(engine, _run_openai_whisper)

    try:
        segments, result, used_partial = runner(params, q, cancel_event)
    except _EngineNotAvailable as e:
        q.put(("error", str(e)))
        return
    except _CancelledByUser:
        q.put(("cancelled", None))
        return
    except Exception as e:
        q.put(("error",
               f"{type(e).__name__}: {e}\n\n"
               + traceback.format_exc()))
        return

    if not segments:
        if used_partial:
            q.put(("cancelled",
                   "Stopped before any segments were transcribed - "
                   "nothing to save."))
        else:
            q.put(("error", "No speech was detected in the file."))
        return

    paragraphs = paragraphify(segments, params["gap"])
    out_path = Path(params["output"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if params.get("review_before_save"):
        word_conf = (_extract_word_conf(result)
                     if params.get("highlight_confidence") else None)
        q.put((
            "paragraphs_ready",
            {
                "paragraphs": paragraphs,
                "out_path": str(out_path),
                "show_timestamp": params.get("show_timestamp", True),
                "title": params.get("title"),
                "output_format": params.get("output_format", "txt"),
                "used_partial": used_partial,
                "result": result if not used_partial else None,
                "extra_formats": params.get("extra_formats") or [],
                "word_conf": word_conf,
                "audio_path": params["input"],
            },
        ))
        return

    try:
        write_paragraphs_to_file(
            paragraphs, out_path,
            show_timestamp=params.get("show_timestamp", True),
            title=params.get("title"),
            output_format=params.get("output_format", "txt"),
            speakers=None,
        )
    except ImportError as e:
        q.put(("error", str(e)))
        return

    prefix = "Partial: w" if used_partial else "W"
    q.put(("log",
           f"{prefix}rote {len(paragraphs)} paragraphs "
           f"(from {len(segments)} segments)\n"
           f"to: {out_path}\n"))

    extra_formats = params.get("extra_formats") or []
    if result and extra_formats:
        _write_extra_formats(result, out_path, extra_formats, q)

    q.put(("done", str(out_path)))


def _run_openai_whisper(params, q, cancel_event):
    """Run the reference OpenAI Whisper implementation. Returns
    (segments_list, result_dict, used_partial)."""
    captured_segments = []
    used_partial = False

    q.put(("log", "Importing whisper...\n"))
    try:
        import whisper
        import tqdm
    except ImportError as e:
        raise _EngineNotAvailable(
            "Could not load openai-whisper:\n"
            f"  {type(e).__name__}: {e}\n\n"
            "Install or repair it with:\n  pip install --upgrade "
            "openai-whisper\n\n"
            "If you are using a virtual environment, make sure this app "
            "is launched from within that environment.")

    q.put(("log", f"Loading model '{params['model']}'...\n"))
    t0 = time.time()
    model = whisper.load_model(params["model"])
    q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))

    if cancel_event.is_set():
        return [], None, True

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    t0 = time.time()

    kwargs = dict(
        language=params["language"],
        task=params["task"],
        temperature=params["temperature"],
        compression_ratio_threshold=params["compression_ratio_threshold"],
        logprob_threshold=params["logprob_threshold"],
        no_speech_threshold=params["no_speech_threshold"],
        condition_on_previous_text=params["condition_on_previous_text"],
        word_timestamps=params["word_timestamps"],
        verbose=True,
    )
    if params.get("beam_size") and params["beam_size"] > 1:
        kwargs["beam_size"] = params["beam_size"]
    if params.get("best_of"):
        kwargs["best_of"] = params["best_of"]
    if params.get("initial_prompt"):
        kwargs["initial_prompt"] = params["initial_prompt"]

    ts_re = re.compile(
        r"\[(\d+):(\d+(?:\.\d+)?)\s*-->\s*(\d+):(\d+(?:\.\d+)?)\]\s*(.*)"
    )

    class _CapturingWriter:
        def __init__(self, q_, audio_duration_, t0_):
            self.q = q_
            self._buf = ""
            self.audio_duration = audio_duration_
            self.transcribe_start = t0_

        def write(self, text):
            if not text:
                return
            self.q.put(("log", text))
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                m = ts_re.search(line)
                if m:
                    sm, ss, em, es, body = m.groups()
                    start = int(sm) * 60 + float(ss)
                    end = int(em) * 60 + float(es)
                    body = body.strip()
                    if body:
                        captured_segments.append((start, end, body))
                        if self.audio_duration and end >= 5.0:
                            wall = time.time() - self.transcribe_start
                            if wall > 0:
                                speed = end / wall
                                remaining_audio = max(
                                    0.0, self.audio_duration - end)
                                eta = remaining_audio / speed if speed > 0 else 0
                                self.q.put((
                                    "eta",
                                    {
                                        "audio_done": end,
                                        "audio_total": self.audio_duration,
                                        "wall_elapsed": wall,
                                        "eta_seconds": eta,
                                        "speed": speed,
                                    },
                                ))

        def flush(self):
            pass

    audio_duration = params.get("audio_duration")
    writer = _CapturingWriter(q, audio_duration, t0)

    original_update = tqdm.tqdm.update

    def cancelling_update(self_, n=1):
        if cancel_event.is_set():
            raise _CancelledByUser()
        return original_update(self_, n)

    tqdm.tqdm.update = cancelling_update
    try:
        with contextlib.redirect_stdout(writer), \
             contextlib.redirect_stderr(writer):
            try:
                result = model.transcribe(params["input"], **kwargs)
            except _CancelledByUser:
                used_partial = True
                result = {"segments": []}
    finally:
        tqdm.tqdm.update = original_update

    if used_partial:
        q.put(("log",
               "\n[Stopped by user - saving what was transcribed so far]\n"))
        segments = list(captured_segments)
    else:
        q.put(("log", f"\n  transcribed in {time.time() - t0:.1f}s\n\n"))
        segments = [
            (float(s["start"]), float(s["end"]),
             (s.get("text") or "").strip())
            for s in result.get("segments", [])
            if (s.get("text") or "").strip()
        ]

    return segments, result, used_partial


def _run_faster_whisper(params, q, cancel_event):
    """Run faster-whisper (CTranslate2-based). Same model names as the
    reference engine. Cancellation between segments via cancel_event."""
    q.put(("log", "Importing faster-whisper...\n"))
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise _EngineNotAvailable(
            "Could not load faster-whisper:\n"
            f"  {type(e).__name__}: {e}\n\n"
            "Install or repair it with:\n  pip install --upgrade "
            "faster-whisper")

    q.put(("log", f"Loading model '{params['model']}'...\n"))
    t0 = time.time()
    # device="auto" picks cuda if available, else cpu.
    # compute_type="auto" picks float16 on cuda, int8 on cpu - both fast,
    # both essentially indistinguishable from float32 for our use.
    model = WhisperModel(
        params["model"], device="auto", compute_type="auto")
    q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))

    if cancel_event.is_set():
        return [], None, True

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    t0 = time.time()

    kwargs = dict(
        language=params.get("language"),
        task=params.get("task", "transcribe"),
        temperature=params.get("temperature", 0.0),
        compression_ratio_threshold=params.get(
            "compression_ratio_threshold"),
        log_prob_threshold=params.get("logprob_threshold"),
        no_speech_threshold=params.get("no_speech_threshold"),
        condition_on_previous_text=params.get(
            "condition_on_previous_text", True),
        word_timestamps=params.get("word_timestamps", False),
    )
    if params.get("beam_size") and params["beam_size"] > 1:
        kwargs["beam_size"] = params["beam_size"]
    if params.get("best_of"):
        kwargs["best_of"] = params["best_of"]
    if params.get("initial_prompt"):
        kwargs["initial_prompt"] = params["initial_prompt"]

    audio_duration = params.get("audio_duration")
    captured = []
    seg_dicts = []
    used_partial = False

    segments_iter, info = model.transcribe(params["input"], **kwargs)
    if audio_duration is None and getattr(info, "duration", None):
        audio_duration = float(info.duration)

    for seg in segments_iter:
        if cancel_event.is_set():
            used_partial = True
            q.put(("log",
                   "\n[Stopped by user - saving what was transcribed so "
                   "far]\n"))
            break
        text = (seg.text or "").strip()
        if not text:
            continue
        start = float(seg.start)
        end = float(seg.end)
        captured.append((start, end, text))
        # Normalise faster-whisper's Word objects to the dict shape the rest
        # of the code (extra-format writers, _extract_word_conf) expects.
        seg_words = [
            {"word": w.word, "start": w.start, "end": w.end,
             "probability": getattr(w, "probability", None)}
            for w in (getattr(seg, "words", None) or [])
        ]
        seg_dicts.append(
            {"start": start, "end": end, "text": text, "words": seg_words})
        q.put(("log",
               f"[{format_timestamp(start)} --> {format_timestamp(end)}]  "
               f"{text}\n"))
        if audio_duration and end >= 5.0:
            wall = time.time() - t0
            if wall > 0:
                speed = end / wall
                remaining = max(0.0, audio_duration - end)
                eta = remaining / speed if speed > 0 else 0
                q.put(("eta", {
                    "audio_done": end,
                    "audio_total": audio_duration,
                    "wall_elapsed": wall,
                    "eta_seconds": eta,
                    "speed": speed,
                }))

    if not used_partial:
        q.put(("log", f"\n  transcribed in {time.time() - t0:.1f}s\n\n"))

    # Build a whisper-compatible result for the extra-format writers and the
    # word-confidence extractor.
    result = {
        "segments": seg_dicts,
        "language": getattr(info, "language", None)
                    or params.get("language") or "",
    }
    return captured, result, used_partial


def _run_mlx_whisper(params, q, cancel_event):
    """Run mlx-whisper (Apple Silicon). No mid-run cancellation - the
    runtime doesn't expose a hook for it - so Stop only applies after the
    transcription finishes."""
    q.put(("log", "Importing mlx-whisper...\n"))
    try:
        import mlx_whisper
    except ImportError as e:
        # find_spec sees the package's metadata but the actual import
        # may fail because a dependency (typically Apple's `mlx`
        # framework) failed to install or isn't supported on this OS
        # version. Surface the real error so the user can act on it.
        raise _EngineNotAvailable(
            "Could not load mlx-whisper:\n"
            f"  {type(e).__name__}: {e}\n\n"
            "If the installer reported a warning about mlx, that's the "
            "underlying cause. To debug or repair:\n"
            "  cd '~/Library/Application Support/Transcribr'\n"
            "  venv/bin/pip install --upgrade mlx mlx-whisper\n\n"
            "Note: mlx-whisper requires macOS 13.5+ on Apple Silicon "
            "(M1, M2, M3, M4).")

    repo = _mlx_repo_for(params["model"])
    q.put(("log",
           f"Using model '{params['model']}' (mlx repo: {repo})\n"))
    q.put(("log",
           "Note: mlx-whisper doesn't support mid-run cancellation; the "
           "Stop button only takes effect after the run completes.\n\n"))

    if cancel_event.is_set():
        return [], None, True

    kwargs = dict(
        path_or_hf_repo=repo,
        language=params.get("language"),
        task=params.get("task", "transcribe"),
        temperature=params.get("temperature", 0.0),
        condition_on_previous_text=params.get(
            "condition_on_previous_text", True),
        word_timestamps=params.get("word_timestamps", False),
        verbose=False,
    )
    if params.get("compression_ratio_threshold") is not None:
        kwargs["compression_ratio_threshold"] = params[
            "compression_ratio_threshold"]
    if params.get("logprob_threshold") is not None:
        kwargs["logprob_threshold"] = params["logprob_threshold"]
    if params.get("no_speech_threshold") is not None:
        kwargs["no_speech_threshold"] = params["no_speech_threshold"]
    if params.get("initial_prompt"):
        kwargs["initial_prompt"] = params["initial_prompt"]

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    t0 = time.time()
    result = mlx_whisper.transcribe(params["input"], **kwargs)
    q.put(("log", f"\n  transcribed in {time.time() - t0:.1f}s\n\n"))

    captured = [
        (float(s["start"]), float(s["end"]), (s.get("text") or "").strip())
        for s in result.get("segments", [])
        if (s.get("text") or "").strip()
    ]
    used_partial = cancel_event.is_set()  # cancel pressed mid-run; we
                                          # still finished, but mark it so
                                          # the GUI handles it like Stop.
    return captured, result, used_partial


def _write_docx(paragraphs, out_path, *, show_timestamp=True, title=None,
                speakers=None):
    """Write paragraphs to a .docx file with a page-numbered footer and an
    italic disclaimer at the end.

    If `title` is given, it appears in bold at the top of the document
    before the transcript paragraphs.

    If `speakers` is given (a list parallel to paragraphs), each paragraph
    can be prefixed by a bold speaker label on its own line. To match
    legal-transcript convention, the label is only emitted when it differs
    from the previous paragraph's speaker (so a string of paragraphs from
    one speaker reads as a single labelled block).

    When `show_timestamp` is True (default), each paragraph is rendered as
    timestamp + tab + body, with a hanging indent so the timestamp sits in
    the left margin column and the body text wraps further right.

    When `show_timestamp` is False, paragraphs are rendered as flat prose
    with no indent."""
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    from docx.enum.section import WD_ORIENT

    doc = Document()

    # Force A4 portrait. python-docx defaults to US Letter (8.5x11"), so set
    # the page dimensions explicitly. A4 is 21.0 x 29.7 cm.
    for section in doc.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        # Slightly tighter margins to leave more room for the indented body.
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # Title at the top, if given. Bold, slightly larger than body, and
    # using the same monospaced font so the document has a consistent
    # typographic feel. A blank paragraph after the title gives the
    # transcript visual breathing room.
    if title:
        title_para = doc.add_paragraph()
        title_run = title_para.add_run(title)
        title_run.bold = True
        title_run.font.name = "Courier New"
        title_run.font.size = Pt(12)
        doc.add_paragraph()  # blank line between title and transcript

    # Body paragraph style: hanging indent (only useful when a timestamp
    # sits in the indent column).
    body_style = doc.styles.add_style("Transcript", WD_STYLE_TYPE.PARAGRAPH)
    pf = body_style.paragraph_format
    if show_timestamp:
        pf.left_indent = Cm(3.0)
        pf.first_line_indent = -Cm(3.0)
    pf.space_after = Pt(6)
    body_style.font.name = "Courier New"
    body_style.font.size = Pt(10)

    # Speaker label style: bold, no indent, slight breathing room before so
    # successive turns are visually separated.
    label_style = doc.styles.add_style("SpeakerLabel", WD_STYLE_TYPE.PARAGRAPH)
    label_style.font.name = "Courier New"
    label_style.font.size = Pt(10)
    label_style.font.bold = True
    label_style.paragraph_format.space_before = Pt(8)
    label_style.paragraph_format.space_after = Pt(0)
    # Keep the speaker label on the same page as the paragraph it
    # introduces. Without this Word can leave an orphan label at the
    # bottom of a page with its body paragraph starting overleaf.
    label_style.paragraph_format.keep_with_next = True

    last_speaker = None
    for i, para in enumerate(paragraphs):
        start = para[0][0]
        body = " ".join(seg[2] for seg in para).lstrip()
        speaker = speakers[i] if speakers and i < len(speakers) else None

        # Emit a speaker label whenever it changes. For named speakers we
        # emit their name; for an unattributed paragraph that follows a
        # named one, emit an explicit "[Unattributed]" marker so re-parsing
        # the file doesn't carry the previous speaker over.
        if speaker and speaker != last_speaker:
            doc.add_paragraph(speaker, style="SpeakerLabel")
        elif speaker is None and last_speaker is not None:
            doc.add_paragraph(UNATTRIBUTED_LABEL, style="SpeakerLabel")
        last_speaker = speaker

        p = doc.add_paragraph(style="Transcript")
        if show_timestamp:
            p.add_run(format_timestamp(start) + "\t" + body)
        else:
            p.add_run(body)

    # Italic disclaimer at the end. Plain (non-indented) paragraph.
    doc.add_paragraph()  # blank spacer line
    disc = doc.add_paragraph()
    disc_run = disc.add_run(
        "Transcribed using Transcribr - (c) James Leaver, 2026. "
        "If this text has not been deleted by the person who prepared this "
        "document, then the accuracy of this transcript may not have been "
        "checked by a human."
    )
    disc_run.italic = True

    # Footer: "Page X of Y" right-aligned, in Courier New so it matches
    # the body font. python-docx doesn't expose Word field codes
    # directly so we drop in the raw XML.
    def _add_field(paragraph, instr):
        run = paragraph.add_run()
        run.font.name = "Courier New"
        run.font.size = Pt(10)
        f1 = OxmlElement("w:fldChar"); f1.set(qn("w:fldCharType"), "begin")
        it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve")
        it.text = instr
        f2 = OxmlElement("w:fldChar"); f2.set(qn("w:fldCharType"), "end")
        run._r.append(f1); run._r.append(it); run._r.append(f2)

    def _styled_run(paragraph, text):
        run = paragraph.add_run(text)
        run.font.name = "Courier New"
        run.font.size = Pt(10)
        return run

    fp = doc.sections[0].footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _styled_run(fp, "Page ")
    _add_field(fp, " PAGE ")
    _styled_run(fp, " of ")
    _add_field(fp, " NUMPAGES ")

    doc.save(str(out_path))


def _write_pdf(paragraphs, out_path, *, show_timestamp=True, title=None,
               speakers=None):
    """Write paragraphs to an A4 PDF, visually matching the .docx output:
    Courier body with a hanging timestamp indent, bold speaker labels that
    stay with their paragraph, an italic disclaimer, and a right-aligned
    'Page X of Y' footer."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas as _pdfcanvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    page_w, _page_h = A4

    class _NumberedCanvas(_pdfcanvas.Canvas):
        """Two-pass canvas so the footer can say 'Page X of Y'."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                self.setFont("Courier", 10)
                self.drawRightString(
                    page_w - 2.5 * cm, 1.5 * cm,
                    f"Page {self._pageNumber} of {total}")
                _pdfcanvas.Canvas.showPage(self)
            _pdfcanvas.Canvas.save(self)

    body_style = ParagraphStyle(
        "Transcript",
        fontName="Courier", fontSize=10, leading=12,
        spaceAfter=6,
    )
    if show_timestamp:
        # Hanging indent: timestamp in the left column, wrapped body
        # lines aligned under the body column (mirrors the docx style).
        body_style.leftIndent = 3.0 * cm
        body_style.firstLineIndent = -3.0 * cm
    label_style = ParagraphStyle(
        "SpeakerLabel",
        fontName="Courier-Bold", fontSize=10, leading=12,
        spaceBefore=8, spaceAfter=0, keepWithNext=1,
    )
    title_style = ParagraphStyle(
        "TranscriptTitle",
        fontName="Courier-Bold", fontSize=12, leading=14,
        spaceAfter=12,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        fontName="Courier-Oblique", fontSize=10, leading=12,
        spaceBefore=12,
    )

    story = []
    if title:
        story.append(Paragraph(escape(title), title_style))

    last_speaker = None
    for i, para in enumerate(paragraphs):
        start = para[0][0]
        body = " ".join(seg[2] for seg in para).lstrip()
        speaker = speakers[i] if speakers and i < len(speakers) else None
        if speaker and speaker != last_speaker:
            story.append(Paragraph(escape(speaker), label_style))
        elif speaker is None and last_speaker is not None:
            story.append(Paragraph(escape(UNATTRIBUTED_LABEL), label_style))
        last_speaker = speaker

        if show_timestamp:
            # Non-breaking spaces keep the timestamp and the gap to the
            # body out of reportlab's line-wrapping calculations.
            text = (escape(format_timestamp(start))
                    + "   " + escape(body))
        else:
            text = escape(body)
        story.append(Paragraph(text, body_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        escape(
            "Transcribed using Transcribr - (c) James Leaver, 2026. "
            "If this text has not been deleted by the person who prepared "
            "this document, then the accuracy of this transcript may not "
            "have been checked by a human."
        ),
        disclaimer_style,
    ))

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.5 * cm, bottomMargin=2.5 * cm,
        title=title or Path(out_path).stem,
    )
    doc.build(story, canvasmaker=_NumberedCanvas)


def _write_extra_formats(result, txt_out_path, formats, q):
    """Write SRT/VTT/JSON/TSV next to the paragraph .txt.

    Engine-agnostic: reads from result["segments"] which every engine
    runner normalises to a list of {"start", "end", "text"} dicts."""
    segments = (result or {}).get("segments", []) if result else []
    if not segments:
        q.put(("log", "Note: no segments available for extra outputs.\n"))
        return

    output_dir = txt_out_path.parent
    stem = txt_out_path.stem
    for suffix in (".transcript", ".paragraphs"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    for fmt in formats:
        out = output_dir / f"{stem}.{fmt}"
        try:
            if fmt == "json":
                _write_json_result(out, result)
            elif fmt == "srt":
                _write_srt(out, segments)
            elif fmt == "vtt":
                _write_vtt(out, segments)
            elif fmt == "tsv":
                _write_tsv(out, segments)
            else:
                q.put(("log",
                       f"WARNING: unknown extra format '{fmt}', skipped.\n"))
                continue
            q.put(("log", f"Wrote: {out.name}\n"))
        except Exception as e:
            q.put(("log",
                   f"WARNING: failed to write .{fmt}: "
                   f"{type(e).__name__}: {e}\n"))


def _seg_field(seg, key):
    """Pull start/end/text from either a dict or an object segment."""
    if isinstance(seg, dict):
        return seg.get(key)
    return getattr(seg, key, None)


def _format_srt_time(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _format_vtt_time(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _write_srt(path, segments):
    with open(path, "w", encoding="utf-8") as fh:
        for i, seg in enumerate(segments, start=1):
            start = _seg_field(seg, "start") or 0
            end = _seg_field(seg, "end") or 0
            text = (_seg_field(seg, "text") or "").strip()
            fh.write(f"{i}\n")
            fh.write(f"{_format_srt_time(start)} --> "
                     f"{_format_srt_time(end)}\n")
            fh.write(f"{text}\n\n")


def _write_vtt(path, segments):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for seg in segments:
            start = _seg_field(seg, "start") or 0
            end = _seg_field(seg, "end") or 0
            text = (_seg_field(seg, "text") or "").strip()
            fh.write(f"{_format_vtt_time(start)} --> "
                     f"{_format_vtt_time(end)}\n")
            fh.write(f"{text}\n\n")


def _write_tsv(path, segments):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("start\tend\ttext\n")
        for seg in segments:
            start = _seg_field(seg, "start") or 0
            end = _seg_field(seg, "end") or 0
            text = (_seg_field(seg, "text") or "").strip().replace("\t", " ")
            fh.write(f"{int(float(start) * 1000)}\t"
                     f"{int(float(end) * 1000)}\t{text}\n")


def _write_json_result(path, result):
    """Dump the full result dict as JSON. Coerces any non-serialisable
    attributes (e.g. faster-whisper named tuples) to plain dicts."""
    import json

    def _coerce(obj):
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, dict):
            return {k: _coerce(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_coerce(v) for v in obj]
        if hasattr(obj, "_asdict"):
            return _coerce(obj._asdict())
        if hasattr(obj, "__dict__"):
            return _coerce(vars(obj))
        return str(obj)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_coerce(result), fh, indent=2)


# =====================================================================
# GUI
# =====================================================================

class ReviewPane(ttk.Frame):
    """An interactive review/edit pane shown after a transcription finishes.

    The user assigns each paragraph to a speaker (A/B/C/D), optionally
    renames the speakers (e.g. "A" -> "CONSTABLE MACKLEBUM"), and can
    merge a paragraph into its predecessor with the M key.

    When the user clicks Save, `on_save(paragraphs, speakers)` is called.
    When the user clicks Cancel, `on_cancel()` is called. Both callbacks
    are passed in by the parent, so this widget doesn't know about file
    paths or the docx writer.
    """

    MAX_SPEAKERS = 9
    SPEAKER_LETTERS = [str(i) for i in range(1, MAX_SPEAKERS + 1)]
    DEFAULT_NAMES = {str(i): f"Speaker {i}" for i in range(1, MAX_SPEAKERS + 1)}
    # Background colours for each speaker. Picked to be subtle so the
    # text remains the visual focus.
    SPEAKER_COLOURS = {
        "1": "#fff4d6",   # warm yellow
        "2": "#dfeeff",   # soft blue
        "3": "#e3f4d8",   # soft green
        "4": "#f9d9e7",   # soft pink
        "5": "#e7ddf6",   # lavender
        "6": "#ffe0c2",   # peach
        "7": "#d3f0ee",   # teal
        "8": "#f0e4c8",   # tan
        "9": "#d9e8c0",   # olive
    }

    def __init__(self, parent, paragraphs, *, on_save, on_cancel,
                 show_timestamp=True, loaded=False, on_save_revision=None):
        super().__init__(parent)
        self.paragraphs = list(paragraphs)
        # Speaker letter (A/B/C/D) per paragraph, or None.
        self.speakers = [None] * len(self.paragraphs)
        # Speaker letter -> human name. Initially "Speaker A" etc.
        self.speaker_names = dict(self.DEFAULT_NAMES)
        self.show_timestamp = show_timestamp
        self.on_save_cb = on_save
        self.on_cancel_cb = on_cancel
        self.on_save_revision_cb = on_save_revision
        self.loaded = loaded
        self.selected_idx = 0 if self.paragraphs else None
        # Per-paragraph metadata, parallel to self.paragraphs. Each entry is a
        # dict with at least: idx, y, height, body_full. When the row is
        # currently rendered (i.e. inside the scroll viewport), the dict also
        # carries widget references (row, badge, ts, body, _resize, ...) and a
        # canvas window_id. Use _ensure_rendered(idx) to guarantee widgets
        # exist; use _is_rendered(idx) to test.
        self.row_widgets = [
            {
                "idx": i,
                "body_full": " ".join(seg[2] for seg in para).strip(),
                "y": 0,
                "height": 0,
            }
            for i, para in enumerate(self.paragraphs)
        ]
        # Total estimated content height; updated by _compute_layout.
        self._content_height = 0
        # Pixels eaten by row chrome (badge + timestamp + paddings + borders)
        # at the current canvas width; measured by _measure_chrome.
        self._chrome_px = 90
        # Pixels of buffer rendered above and below the visible viewport.
        # Larger = smoother scrolling, more memory.
        self._render_buffer_px = 600
        self._layout_pending = False
        # When edit mode is active for a paragraph, this is its index;
        # otherwise None. Only one row can be in edit mode at a time.
        self.editing_idx = None
        # When entering edit mode, we snapshot the body text so Esc can
        # restore it. Cleared when edit mode exits.
        self._edit_original_text = None

        self._build_ui()
        # Initial layout & render runs after the canvas is laid out (so we
        # know its width). _build_ui hooks the canvas <Configure>.

    # ----- UI construction --------------------------------------------------

    def _build_ui(self):
        # Header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(
            header,
            text="Review and label speakers",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(side="left")
        ttk.Label(
            header,
            text=f"  ({len(self.paragraphs)} paragraphs)",
            foreground="gray",
        ).pack(side="left")

        # Speaker name editor
        names_frame = ttk.LabelFrame(self, text="Speaker names", padding=6)
        names_frame.pack(fill="x", padx=10, pady=4)
        self.name_vars = {}
        for col, letter in enumerate(self.SPEAKER_LETTERS):
            sub = ttk.Frame(names_frame)
            sub.grid(row=col // 2, column=col % 2, sticky="ew", padx=4, pady=2)
            names_frame.columnconfigure(col % 2, weight=1)
            badge = tk.Label(
                sub, text=letter, width=2, font=("TkDefaultFont", 10, "bold"),
                bg=self.SPEAKER_COLOURS[letter], fg="black",
                relief="solid", borderwidth=1,
            )
            badge.pack(side="left", padx=(0, 6))
            var = tk.StringVar(value=self.speaker_names[letter])
            self.name_vars[letter] = var
            entry = ttk.Entry(sub, textvariable=var)
            entry.pack(side="left", fill="x", expand=True)
            var.trace_add("write", lambda *_, L=letter: self._on_name_changed(L))

        # Scrollable paragraph list
        list_frame = ttk.LabelFrame(self, text="Paragraphs", padding=4)
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.canvas = tk.Canvas(list_frame, highlightthickness=0,
                                background="white",
                                takefocus=True)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                  command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        # yscrollcommand is wired below so we can hook the visible-row
        # updater alongside the scrollbar's normal indicator update.

        # Body font (also used for height estimation).
        import tkinter.font as tkfont
        self._body_font = tkfont.Font(family="TkDefaultFont", size=10)
        self._line_height = self._body_font.metrics("linespace")
        # Cached canvas width used when laying out rows. Updated on every
        # <Configure>. Layout uses _line_height + padding to estimate row
        # heights without rendering them.
        self._canvas_width = 0

        # Resize -> recompute layout (heights depend on width) and
        # re-place rendered rows; also update visible set.
        def _on_canvas_configure(event):
            new_width = event.width
            if new_width <= 1:
                return
            if new_width != self._canvas_width:
                self._canvas_width = new_width
                # Recompute estimated heights against the new width and
                # shift existing rows. Defer to idle to coalesce successive
                # Configure events during a window drag.
                self._schedule_relayout()
            else:
                self._update_visible_rows()
        self.canvas.bind("<Configure>", _on_canvas_configure)

        # Update visible rows whenever the canvas's view changes (scroll).
        # We hook via yscrollcommand below so the scrollbar still updates.
        def _yscrollcommand_with_visible(first, last):
            scrollbar.set(first, last)
            self._update_visible_rows()
        self.canvas.configure(yscrollcommand=_yscrollcommand_with_visible)

        # Mousewheel scrolling. We have to handle a few platform quirks:
        #
        # - Windows: <MouseWheel> with event.delta as integers in
        #   multiples of 120. One wheel notch = +/-120. Trackpad
        #   precision scrolling can send smaller multiples.
        # - macOS: <MouseWheel> with event.delta as a small *float* for
        #   trackpads (often 0.1 - 10.0 with smooth scrolling). Mice
        #   typically send integer deltas around 1-3.
        # - Linux X11: classic <Button-4>/<Button-5> for each notch, no
        #   delta. Trackpads under X11 also emit Button-4/5.
        #
        # The hardest case is macOS trackpad: the events arrive thick and
        # fast with sub-unit deltas. Naively passing those to
        # yview_scroll(int(delta), "units") rounds many of them to zero,
        # so a slow swipe doesn't move at all. We fix this by
        # accumulating the floating-point movement and only calling
        # yview_scroll once we've accumulated at least one whole unit's
        # worth.
        #
        # Also: yview_scroll requires an integer first argument. Passing
        # a float raises TclError, which Tk silently swallows in some
        # contexts - so the user just sees nothing happen. We round to
        # int explicitly and wrap the call in a try/except defensively.
        self._scroll_accumulator = 0.0

        def _on_mousewheel(event):
            # Linux Button-4/5: one unit per event, fixed direction.
            num = getattr(event, "num", 0)
            if num == 4:
                try:
                    self.canvas.yview_scroll(-1, "units")
                except tk.TclError:
                    pass
                return "break"
            if num == 5:
                try:
                    self.canvas.yview_scroll(1, "units")
                except tk.TclError:
                    pass
                return "break"

            # MouseWheel with a delta. Normalise across platforms.
            delta = getattr(event, "delta", 0)
            if not delta:
                return "break"

            # Compute units of movement. Negative = scroll down, positive
            # = scroll up; we negate at the end since yview_scroll uses
            # the opposite convention (positive arg = scroll content up).
            if abs(delta) >= 100:
                # Windows-style 120-per-notch. One unit per notch.
                step = delta / 120.0
            else:
                # macOS / fine-grained: scale down so a small swipe maps
                # to a small movement. Empirically delta/3 feels close to
                # other macOS apps; clamp to ±5 per event so a violent
                # swipe doesn't teleport.
                step = max(-5.0, min(5.0, delta / 3.0))

            # Accumulate fractional movement across events so slow
            # trackpad swipes still register.
            self._scroll_accumulator += step
            whole_units = int(self._scroll_accumulator)
            if whole_units == 0:
                return "break"
            self._scroll_accumulator -= whole_units
            try:
                self.canvas.yview_scroll(-whole_units, "units")
            except tk.TclError:
                pass
            return "break"

        # Bind on the canvas. Per-row body widgets get the same binding
        # forwarded inside _construct_row so wheel events delivered to a
        # body Text don't get consumed by Tk's class binding.
        self.canvas.bind("<MouseWheel>", _on_mousewheel)
        self.canvas.bind("<Button-4>", _on_mousewheel)
        self.canvas.bind("<Button-5>", _on_mousewheel)
        # Stash the handler so per-row body widgets can also bind to it.
        self._on_mousewheel = _on_mousewheel

        # Help line
        help_line = ttk.Label(
            self,
            text=(
                "Up/Down navigate  ·  1-9 set speaker  ·  0 clear  ·  "
                "M merge with previous  ·  Double-click a word to split  ·  "
                "Enter to edit text"
            ),
            foreground="gray",
        )
        help_line.pack(fill="x", padx=10, pady=(2, 4))

        # Action buttons
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(4, 10))
        if self.loaded:
            ttk.Button(actions, text="Save (overwrite original)",
                       command=self._on_save_clicked).pack(side="left")
            if self.on_save_revision_cb is not None:
                ttk.Button(actions, text="Save as revision...",
                           command=self._on_save_revision_clicked
                           ).pack(side="left", padx=(8, 0))
            ttk.Button(actions, text="Close without saving",
                       command=self._on_cancel_clicked).pack(side="right")
        else:
            ttk.Button(actions, text="Save with labels",
                       command=self._on_save_clicked).pack(side="left")
            ttk.Button(actions, text="Save without labels",
                       command=self._on_cancel_clicked).pack(side="right")

        # Keyboard bindings - on the toplevel so they work everywhere within
        # the review pane regardless of focus. We guard each handler with
        # _is_text_input_focused() so e.g. typing "1" in the speaker-name
        # field doesn't also assign speaker 1 to the selected paragraph.
        top = self.winfo_toplevel()
        top.bind("<Up>", self._on_arrow_up)
        top.bind("<Down>", self._on_arrow_down)
        for letter_idx, letter in enumerate(self.SPEAKER_LETTERS, start=1):
            top.bind(str(letter_idx),
                     lambda e, L=letter: self._kb_set_speaker(L))
        top.bind("0", lambda e: self._kb_set_speaker(None))
        top.bind("<KeyPress-m>", lambda e: self._kb_merge())
        top.bind("<KeyPress-M>", lambda e: self._kb_merge())
        # Return enters edit mode for the selected paragraph. F2 is
        # kept as an alias because users coming from spreadsheet apps
        # (Excel, Sheets, Numbers) reach for F2 reflexively.
        top.bind("<Return>", lambda e: self._kb_edit())
        top.bind("<F2>", lambda e: self._kb_edit())

    # ----- Virtualization ---------------------------------------------------
    #
    # For long transcripts (e.g. 1000+ paragraphs) creating a Tk widget
    # tree per paragraph hangs or crashes Tk. Instead we keep lightweight
    # metadata per paragraph and only build widgets for rows currently
    # inside (or close to) the visible viewport. As the user scrolls,
    # rows entering the viewport are constructed and rows leaving it are
    # destroyed.

    # Padding contributions to a row's height: row vertical padding
    # (pady=1 above + 1 below from .place outer = 4) plus the body's
    # internal pady=4 above + 4 below = 8, plus row borders. Empirically
    # ~14 pixels of chrome per row.
    _ROW_CHROME_PX = 14

    def _is_rendered(self, idx):
        return (0 <= idx < len(self.row_widgets)
                and "row" in self.row_widgets[idx])

    def _ensure_rendered(self, idx):
        if not (0 <= idx < len(self.row_widgets)):
            return
        if "row" not in self.row_widgets[idx]:
            self._render_row(idx)

    def _measure_chrome(self):
        """Build a temporary row off-screen, measure how many pixels of
        the canvas width are eaten by chrome (badge, timestamp, paddings,
        borders), and cache the result. Subsequent _render_row calls use
        the cached value to compute the body's wrapped line count
        accurately on first paint, so rows don't visibly resize when
        they scroll into view."""
        if self._canvas_width <= 1:
            return
        sample_para = next((p for p in self.paragraphs if p), None)
        if sample_para is None:
            self._chrome_px = 90  # nothing to measure; fall back
            return
        # Use a representative timestamp; pick whichever is wider:
        # [H:MM:SS] is widest, so prefer it if any paragraph has hours.
        max_start = max((p[0][0] for p in self.paragraphs if p), default=0.0)
        ts_text = format_timestamp(max_start) if self.show_timestamp else ""
        # Build the same widget tree as _render_row but minus the body.
        probe = tk.Frame(self.canvas, background="white",
                         highlightthickness=2)
        badge = tk.Label(probe, text="-", width=2,
                         font=("TkDefaultFont", 10, "bold"),
                         bg="white", fg="black",
                         relief="solid", borderwidth=1)
        badge.pack(side="left", padx=(4, 8), pady=4, anchor="n")
        ts = tk.Label(probe, text=ts_text, foreground="#666",
                      font=("Courier", 9), background="white", anchor="nw")
        ts.pack(side="left", padx=(0, 8), pady=4, anchor="n")
        # Place it off-screen so the user never sees it.
        wid = self.canvas.create_window(
            -10000, -10000, anchor="nw", window=probe,
            width=self._canvas_width)
        self.canvas.update_idletasks()
        try:
            badge_w = badge.winfo_width()
            ts_w = ts.winfo_width()
        except tk.TclError:
            badge_w = ts_w = 0
        # The body fills whatever is left of the row Frame after the
        # 2px highlight on each side, the badge (with 4+8 padx around it),
        # and the timestamp (with 0+8 padx around it).
        chrome = (2 + 4 + badge_w + 8
                  + 0 + ts_w + 8 + 2)
        if chrome > 0:
            self._chrome_px = chrome
        try:
            self.canvas.delete(wid)
            probe.destroy()
        except tk.TclError:
            pass

    def _body_width_estimate(self):
        return max(1, self._canvas_width - getattr(self, "_chrome_px", 90))

    def _estimate_row_height(self, idx):
        """Estimate a row's total height in pixels for the current canvas
        width. Used during layout when the row is not rendered."""
        rw = self.row_widgets[idx]
        text = rw.get("body_full") or ""
        lines = self._count_wrapped_lines(
            text, self._body_font, self._body_width_estimate())
        return self._line_height * lines + self._ROW_CHROME_PX

    def _compute_layout(self):
        """Recompute y position of every row from estimated heights, and
        update the canvas scroll region to match. Cheap: O(N), no widget
        creation."""
        y = 0
        for rw in self.row_widgets:
            rw["y"] = y
            if rw.get("height", 0) <= 0:
                rw["height"] = self._estimate_row_height(rw["idx"])
            y += rw["height"]
        self._content_height = y
        try:
            self.canvas.configure(
                scrollregion=(0, 0, max(1, self._canvas_width),
                              max(1, self._content_height)))
        except tk.TclError:
            pass

    def _schedule_relayout(self):
        """Coalesce repeated layout requests into one idle call."""
        if self._layout_pending:
            return
        self._layout_pending = True
        def _run():
            self._layout_pending = False
            # Re-measure chrome since it depends on the (just-changed)
            # canvas width and font metrics; cheap, ~1ms.
            self._measure_chrome()
            # Reset heights so they get re-estimated for the new width.
            for rw in self.row_widgets:
                rw["height"] = 0
            self._compute_layout()
            # Re-place currently-rendered rows at their new y positions
            # and update their widget widths.
            inner_w = max(1, self._canvas_width)
            for rw in self.row_widgets:
                wid = rw.get("window_id")
                if wid is not None:
                    try:
                        self.canvas.coords(wid, 0, rw["y"])
                        self.canvas.itemconfigure(wid, width=inner_w)
                    except tk.TclError:
                        pass
            self._update_visible_rows()
        self.after_idle(_run)

    def _update_visible_rows(self):
        """Render rows in the viewport (plus a buffer) and unrender rows
        outside it. Called on scroll, resize, and after any layout change."""
        if self._content_height <= 0 or self._canvas_width <= 1:
            return
        try:
            view_top_px = self.canvas.canvasy(0)
            view_h = self.canvas.winfo_height()
        except tk.TclError:
            return
        view_bot_px = view_top_px + view_h
        buf = self._render_buffer_px
        want_top = view_top_px - buf
        want_bot = view_bot_px + buf

        # Find the index range whose [y, y+height] overlaps [want_top, want_bot].
        # Since rows are sequential we can do a linear scan; for very
        # long transcripts a binary search would be faster, but with
        # only ~1000 rows this is plenty fast.
        first = last = None
        for rw in self.row_widgets:
            top = rw["y"]
            bot = top + rw["height"]
            if bot < want_top:
                continue
            if top > want_bot:
                break
            if first is None:
                first = rw["idx"]
            last = rw["idx"]

        if first is None:
            target_set = set()
        else:
            target_set = set(range(first, last + 1))

        # Always keep rows that have special state (selected, editing)
        # rendered so their widget references stay valid.
        if self.selected_idx is not None:
            target_set.add(self.selected_idx)
        if self.editing_idx is not None:
            target_set.add(self.editing_idx)

        # Compute current rendered set.
        rendered = {rw["idx"] for rw in self.row_widgets if "row" in rw}

        # Render new rows.
        for idx in sorted(target_set - rendered):
            self._render_row(idx)
        # Unrender rows that fell out of range.
        for idx in sorted(rendered - target_set):
            self._unrender_row(idx)

    def _render_row(self, idx):
        """Build the widget tree for paragraph `idx` and place it on the
        canvas at the row's stored y position."""
        rw = self.row_widgets[idx]
        if "row" in rw:
            return
        para = self.paragraphs[idx]
        body_full = " ".join(seg[2] for seg in para).strip()
        rw["body_full"] = body_full

        # Use a constant highlight thickness so toggling selected/editing
        # state only changes the colour, not the row's outer size. With a
        # variable thickness Tk would resize the Frame and invalidate our
        # virtualized layout each time the user moved the cursor.
        row = tk.Frame(self.canvas, background="white",
                       highlightthickness=2, highlightbackground="white",
                       cursor="hand2")
        rw["row"] = row

        # Speaker badge: 1/2/3/4 or "-".
        # Explicit foreground because tk.Label inherits the system text
        # colour by default, which on macOS dark mode is light grey -
        # invisible against our explicit white background.
        badge = tk.Label(row, text="-", width=2,
                        font=("TkDefaultFont", 10, "bold"),
                        bg="white", fg="black",
                        relief="solid", borderwidth=1)
        badge.pack(side="left", padx=(4, 8), pady=4, anchor="n")
        rw["badge"] = badge

        # Timestamp
        start_ts = para[0][0]
        ts_text = format_timestamp(start_ts) if self.show_timestamp else ""
        ts = tk.Label(row, text=ts_text, foreground="#666",
                     font=("Courier", 9), background="white", anchor="nw")
        ts.pack(side="left", padx=(0, 8), pady=4, anchor="n")
        rw["ts"] = ts

        # Body text. tk.Text rather than Label so we can handle
        # double-click-to-split via @x,y indices, and so word-wrap fits
        # any width. Editable only when in edit mode (gated below).
        # Pre-compute the wrapped line count using the precise chrome
        # measurement so the Text widget renders at its final height on
        # first paint - otherwise rows visibly resize after layout.
        initial_lines = self._count_wrapped_lines(
            body_full, self._body_font, self._body_width_estimate())
        body = tk.Text(
            row,
            wrap="word",
            font=self._body_font,
            background="white",
            foreground="black",
            # On macOS dark mode and some Windows themes the default
            # insertbackground (cursor colour) is light - invisible on
            # our explicit white-ish row backgrounds.
            insertbackground="black",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            height=initial_lines,
            padx=0, pady=0,
            spacing1=0, spacing3=0,
        )
        body.insert("1.0", body_full)
        rw["body"] = body

        def _maybe_block_key(event, _r=rw):
            if self.editing_idx == _r["idx"]:
                return None
            return "break"
        body.bind("<Key>", _maybe_block_key)

        def _maybe_block_paste(event, _r=rw):
            if self.editing_idx == _r["idx"]:
                return None
            return "break"
        body.bind("<<Paste>>", _maybe_block_paste)
        body.bind("<<Cut>>", _maybe_block_paste)

        def _on_escape(event, _r=rw):
            if self.editing_idx == _r["idx"]:
                self._cancel_edit()
                return "break"
        def _on_return(event, _r=rw):
            if self.editing_idx == _r["idx"]:
                self._commit_edit()
            else:
                if self.selected_idx != _r["idx"]:
                    self._select_row(_r["idx"])
                self._enter_edit_mode(_r["idx"])
            return "break"
        body.bind("<Escape>", _on_escape)
        body.bind("<Return>", _on_return)

        body.pack(side="left", fill="x", expand=True, pady=4)

        def _click_select(event, _r=rw):
            self._select_row(_r["idx"])
        for w in (row, badge, ts, body):
            w.bind("<Button-1>", _click_select)

        def _maybe_split_or_select(event, _r=rw):
            if self.editing_idx == _r["idx"]:
                return None
            return self._on_body_double_click(event, _r["idx"])
        body.bind("<Double-Button-1>", _maybe_split_or_select)

        # Auto-size the Text widget to fit its current content. After
        # resize completes, propagate height changes back to the
        # virtualized layout (the row's height in row_widgets).
        def _resize_body_now(_r=rw):
            b = _r.get("body")
            r_frame = _r.get("row")
            if b is None or r_frame is None:
                return
            try:
                width_px = b.winfo_width()
                if width_px <= 1:
                    return
                current_text = b.get("1.0", "end-1c")
                lines = self._count_wrapped_lines(current_text,
                                                  self._body_font,
                                                  width_px)
                if b.cget("height") != lines:
                    b.configure(height=lines)
                # Read the row Frame's actual requested height. This is
                # what the row physically occupies, so using it for layout
                # eliminates the visual gaps and selection-ring overlaps
                # that come from estimating chrome with a constant.
                r_frame.update_idletasks()
                actual = r_frame.winfo_reqheight()
                if actual > 0 and abs(actual - _r.get("height", 0)) > 1:
                    self._adjust_row_height(_r["idx"], actual)
            except tk.TclError:
                pass

        def _schedule_resize(event=None, _r=rw):
            pending = _r.get("_resize_after_id")
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except (tk.TclError, ValueError):
                    pass
            _r["_resize_after_id"] = self.after(50, _resize_body_now)

        body.after_idle(_resize_body_now)
        body.bind("<Configure>", _schedule_resize)
        rw["_resize"] = _resize_body_now

        for w in (row, badge, ts, body):
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", self._on_mousewheel)
            w.bind("<Button-5>", self._on_mousewheel)

        # Place the row's Frame on the canvas at the row's stored y.
        inner_w = max(1, self._canvas_width)
        rw["window_id"] = self.canvas.create_window(
            0, rw["y"], anchor="nw", window=row, width=inner_w)
        # Apply selection / speaker styling immediately so the row looks
        # right the moment it appears.
        self._refresh_row(idx)

    def _unrender_row(self, idx):
        """Tear down the widget tree for paragraph `idx`. Keeps the
        metadata entry (idx, y, height, body_full) intact."""
        if not (0 <= idx < len(self.row_widgets)):
            return
        rw = self.row_widgets[idx]
        if "row" not in rw:
            return
        # Cancel any pending after() callback so it doesn't fire on a
        # destroyed widget.
        pending = rw.pop("_resize_after_id", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except (tk.TclError, ValueError):
                pass
        wid = rw.pop("window_id", None)
        if wid is not None:
            try:
                self.canvas.delete(wid)
            except tk.TclError:
                pass
        try:
            rw["row"].destroy()
        except tk.TclError:
            pass
        for k in ("row", "badge", "ts", "body", "_resize"):
            rw.pop(k, None)

    def _reposition_rendered(self):
        """Move every currently-rendered row's canvas window to its
        stored y position. Used after a structural change (merge/split)
        that recomputed the layout."""
        for rw in self.row_widgets:
            wid = rw.get("window_id")
            if wid is not None:
                try:
                    self.canvas.coords(wid, 0, rw["y"])
                except tk.TclError:
                    pass

    def _adjust_row_height(self, idx, new_height):
        """Update a row's stored height to `new_height` and shift the y
        positions of all rows below it by the delta. Rendered rows below
        get their canvas window moved."""
        rw = self.row_widgets[idx]
        delta = new_height - rw.get("height", 0)
        if delta == 0:
            return
        rw["height"] = new_height
        for j in range(idx + 1, len(self.row_widgets)):
            self.row_widgets[j]["y"] += delta
            wid = self.row_widgets[j].get("window_id")
            if wid is not None:
                try:
                    self.canvas.coords(wid, 0, self.row_widgets[j]["y"])
                except tk.TclError:
                    pass
        self._content_height += delta
        try:
            self.canvas.configure(
                scrollregion=(0, 0, max(1, self._canvas_width),
                              max(1, self._content_height)))
        except tk.TclError:
            pass

    def _update_row_content(self, idx):
        """Refresh the text and timestamp displayed in row `idx` to match
        the underlying paragraphs[idx] data. Used after merge/split/edit
        instead of destroying and rebuilding the row widgets.

        Safe to call for unrendered rows: we update body_full + height
        estimate, then return without touching widgets that don't exist."""
        if idx < 0 or idx >= len(self.row_widgets):
            return
        rw = self.row_widgets[idx]
        para = self.paragraphs[idx]
        body_full = " ".join(seg[2] for seg in para).strip()
        rw["body_full"] = body_full
        if "body" not in rw:
            # Row is unrendered. Re-estimate its height so the layout
            # stays roughly correct.
            new_h = self._estimate_row_height(idx)
            self._adjust_row_height(idx, new_h)
            return
        body = rw["body"]
        body.delete("1.0", "end")
        body.insert("1.0", body_full)
        if para:
            start_ts = para[0][0]
            new_ts = format_timestamp(start_ts) if self.show_timestamp else ""
            rw["ts"].config(text=new_ts)
        try:
            rw["_resize"]()
        except (KeyError, tk.TclError):
            pass

    def _reindex_rows(self):
        """After paragraphs/speakers list changes structurally (insert /
        delete), update each row's stored idx so its bindings fire on
        the right paragraph. Cheap because we just touch a dict field
        per row."""
        for new_idx, rw in enumerate(self.row_widgets):
            rw["idx"] = new_idx

    # ----- State updates ----------------------------------------------------

    @staticmethod
    def _count_wrapped_lines(text, font, max_width_px):
        """Return how many visual lines `text` wraps to in a Text widget
        of width `max_width_px` using `font`. Mirrors Tk's word-wrap rules
        closely enough for sizing rows."""
        if not text or max_width_px <= 0:
            return 1
        space_w = font.measure(" ")
        words = text.split()
        if not words:
            return 1
        lines = 1
        line_w = 0
        for w in words:
            ww = font.measure(w)
            if line_w == 0:
                line_w = ww
            elif line_w + space_w + ww > max_width_px:
                lines += 1
                line_w = ww
            else:
                line_w += space_w + ww
        return lines

    def _refresh_all_rows(self):
        for i in range(len(self.paragraphs)):
            self._refresh_row(i)

    def _refresh_row(self, idx):
        if idx < 0 or idx >= len(self.row_widgets):
            return
        rw = self.row_widgets[idx]
        if "row" not in rw:
            return  # not currently rendered; styling applied on next render
        speaker = self.speakers[idx]
        if speaker:
            rw["badge"].config(text=speaker, bg=self.SPEAKER_COLOURS[speaker])
            row_bg = self.SPEAKER_COLOURS[speaker]
        else:
            rw["badge"].config(text="-", bg="white")
            row_bg = "white"
        rw["row"].config(background=row_bg)
        rw["ts"].config(background=row_bg)
        rw["body"].config(background=row_bg)
        # Highlight ring: edit mode (orange) over selection (blue) over
        # the no-ring resting state. Thickness is constant (set in
        # _render_row) so changing state never resizes the row.
        if idx == self.editing_idx:
            rw["row"].config(highlightbackground="#ff9900",
                             highlightcolor="#ff9900")
        elif idx == self.selected_idx:
            rw["row"].config(highlightbackground="#3a7afe",
                             highlightcolor="#3a7afe")
        else:
            rw["row"].config(highlightbackground=row_bg,
                             highlightcolor=row_bg)

    def _select_row(self, idx):
        if self.editing_idx is not None and self.editing_idx != idx:
            self._commit_edit()

        old = self.selected_idx
        self.selected_idx = idx
        # Make sure the target row is rendered (so refresh has widgets to
        # operate on, and so the user actually sees the selection ring).
        self._ensure_rendered(idx)
        if old is not None:
            self._refresh_row(old)
        self._refresh_row(idx)

        # Move keyboard focus to the canvas, but ONLY when we're not in
        # edit mode for the row that was clicked. In edit mode the body
        # Text widget needs to keep focus so the user can type.
        #
        # Without this, focus often stays on whatever Entry/Text widget
        # the user last clicked (e.g. a speaker-name field, or the body
        # Text of the row that was just clicked), and our hotkey
        # handlers see _is_text_input_focused() return True and refuse
        # to fire.
        #
        # We can't use after_idle here because we call update_idletasks()
        # below for scroll-into-view, which would drain the after_idle
        # queue before Tk's class-level <Button-1> binding has had its
        # turn to set focus to the Text widget. Tk's class binding would
        # then override our focus grab, putting focus on the body Text
        # and silently breaking hotkeys.
        #
        # Using after(1, ...) instead schedules the callback for the
        # next real event-loop iteration, after Tk's class binding has
        # already moved focus to the Text. Our focus grab then has the
        # last word.
        if self.editing_idx != idx:
            def _grab_focus():
                try:
                    self.canvas.focus_set()
                except tk.TclError:
                    pass
            self.after(1, _grab_focus)
        # Scroll into view using the virtualized layout's y/height.
        rw = self.row_widgets[idx]
        try:
            total_h = max(1, self._content_height)
            view_h = self.canvas.winfo_height()
            view_top_px = self.canvas.canvasy(0)
            view_bot_px = view_top_px + view_h
            y_top = rw["y"]
            y_bot = y_top + rw["height"]
            if y_top < view_top_px:
                self.canvas.yview_moveto(y_top / total_h)
            elif y_bot > view_bot_px:
                self.canvas.yview_moveto(
                    max(0.0, (y_bot - view_h) / total_h))
            # yview_moveto fires yscrollcommand, which calls
            # _update_visible_rows; nothing else to do here.
        except tk.TclError:
            pass

    def _is_text_input_focused(self):
        """Return True if a text-input widget has focus, so we should
        NOT consume keypresses as review commands."""
        try:
            focused = self.winfo_toplevel().focus_get()
        except (KeyError, tk.TclError):
            return False
        if focused is None:
            return False
        # Entry / ttk.Entry / Text / etc. all expose 'insert' index for the
        # cursor; checking class name is more robust.
        cls = focused.winfo_class()
        return cls in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")

    def _kb_set_speaker(self, letter):
        if self._is_text_input_focused():
            return
        self._set_speaker(letter)

    def _kb_merge(self):
        if self._is_text_input_focused():
            return
        self._merge_with_previous()

    def _kb_edit(self):
        """F2 handler: enter edit mode for the currently selected row.
        Silenced if we're already in an Entry (i.e. the speaker-name
        fields), so users can't accidentally start editing a paragraph
        while typing a speaker name."""
        if self._is_text_input_focused():
            return
        if self.selected_idx is None:
            return
        if self.editing_idx is not None:
            # Already editing: F2 commits the current edit.
            self._commit_edit()
            return
        self._enter_edit_mode(self.selected_idx)

    # ----- Edit mode --------------------------------------------------------

    def _enter_edit_mode(self, idx):
        """Make the body Text widget for paragraph `idx` editable, focus
        it, and visually mark the row as being edited."""
        if idx is None or idx < 0 or idx >= len(self.row_widgets):
            return
        self._ensure_rendered(idx)
        rw = self.row_widgets[idx]
        body = rw["body"]
        # Snapshot the current text so Esc can restore it.
        self._edit_original_text = body.get("1.0", "end-1c")
        self.editing_idx = idx
        # Refresh to draw the edit-mode highlight ring.
        self._refresh_row(idx)
        # Focus the body and place the cursor at the end. Tkinter's
        # default behaviour after focus is to select all the text in the
        # widget, which we don't want - we want a normal editing cursor.
        body.focus_set()
        body.mark_set("insert", "end-1c")
        body.see("insert")
        # Tk's class binding will have placed a selection on Text widget
        # focus; clear it.
        try:
            body.tag_remove("sel", "1.0", "end")
        except tk.TclError:
            pass

    def _commit_edit(self):
        """Read the body text and write it back into the paragraph data,
        then exit edit mode."""
        idx = self.editing_idx
        if idx is None or idx >= len(self.row_widgets):
            self._exit_edit_mode()
            return
        rw = self.row_widgets[idx]
        if "body" not in rw:
            # Editing row was unrendered (shouldn't normally happen since
            # we keep editing_idx in the rendered set). Bail safely.
            self._exit_edit_mode()
            return
        new_text = rw["body"].get("1.0", "end-1c").strip()
        if not new_text:
            # Refuse to commit an empty paragraph - silently discard the
            # edit instead. The alternative (deleting the paragraph) is
            # a destructive action the user didn't explicitly ask for.
            self._cancel_edit()
            return
        self._replace_paragraph_text(idx, new_text)
        # Update the cached body_full so future operations see the
        # current text.
        rw["body_full"] = new_text
        self._exit_edit_mode()
        # The new text may need a different number of visual lines.
        # Call the row's stashed resize function directly.
        try:
            rw["_resize"]()
        except (KeyError, tk.TclError):
            pass

    def _cancel_edit(self):
        """Restore the original text and exit edit mode."""
        idx = self.editing_idx
        if idx is not None and idx < len(self.row_widgets):
            rw = self.row_widgets[idx]
            if "body" in rw:
                original = self._edit_original_text or rw.get("body_full", "")
                rw["body"].delete("1.0", "end")
                rw["body"].insert("1.0", original)
        self._exit_edit_mode()

    def _exit_edit_mode(self):
        """Clear edit-mode state and refresh the row's appearance."""
        idx = self.editing_idx
        self.editing_idx = None
        self._edit_original_text = None
        if idx is not None and idx < len(self.row_widgets):
            self._refresh_row(idx)
        # Return focus to the canvas so hotkeys work again.
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass

    def _replace_paragraph_text(self, idx, new_text):
        """Replace paragraph `idx`'s segments with a single synthetic
        segment containing the edited text, preserving the original
        start/end times."""
        para = self.paragraphs[idx]
        if not para:
            return
        start_t = para[0][0]
        end_t = para[-1][1]
        self.paragraphs[idx] = [(start_t, end_t, new_text)]

    def _on_body_double_click(self, event, idx):
        """User double-clicked a word in the row's body. Split the paragraph
        so the new paragraph starts at that word.

        We don't use Tkinter's built-in 'wordstart' modifier because it
        treats apostrophes and hyphens as word breaks: double-clicking on
        the 'm' of 'I'm' would otherwise yield just 'm' and split there,
        producing a nonsensical paragraph starting with 'm'. Instead we
        walk back from the click position character-by-character, treating
        letters, digits, apostrophes, and hyphens as part of the same word.
        """
        if self.selected_idx != idx:
            self._select_row(idx)
        try:
            text_widget = event.widget
            click_index = text_widget.index(f"@{event.x},{event.y}")
            # Get the absolute character offset of the click point.
            click_offset = len(text_widget.get("1.0", click_index))
        except tk.TclError:
            return

        body_full = text_widget.get("1.0", "end-1c")
        if not body_full or click_offset >= len(body_full):
            return

        # Walk back to the start of the word containing the click. A "word
        # character" here is any letter or digit, plus the in-word
        # punctuation marks ' and - (so "I'm", "don't", "ex-husband" all
        # stay intact).
        def is_word_char(ch):
            return ch.isalnum() or ch in ("'", "\u2019", "-")

        start = click_offset
        # If the click landed exactly between words (on a space), Tk gave
        # us the index of the *next* word's first character; that's fine.
        # Otherwise we walk back while we're inside a word.
        while start > 0 and is_word_char(body_full[start - 1]):
            start -= 1

        if start <= 0:
            # Click was on the first word; splitting there would produce
            # an empty first paragraph. No-op.
            return

        self._do_split_at_offset(idx, start)
        # Tkinter's default double-click behaviour selects the word, which
        # leaves a highlighted range visible after we rebuild rows. Clear
        # any selection on the (now-stale) widget defensively.
        try:
            event.widget.tag_remove("sel", "1.0", "end")
        except tk.TclError:
            pass
        return "break"  # stop the default double-click selection behaviour

    def _do_split_at_offset(self, idx, offset):
        """Split paragraph `idx` so that everything before character `offset`
        stays in paragraph idx, and everything from `offset` onward becomes
        a new paragraph at idx+1.

        We walk the segments of the paragraph, treating them as joined by
        single spaces (matching the body construction). When the split
        falls inside a segment, that segment's text is divided and the
        second half becomes a synthetic single-segment paragraph that
        inherits the parent's start time. (Per design: split paragraphs
        don't need accurate timestamps.)
        """
        if idx >= len(self.paragraphs):
            return
        # If this row is currently being edited, commit the edit so the
        # split operates on the latest text.
        if self.editing_idx == idx:
            self._commit_edit()
        para = self.paragraphs[idx]

        # Build the joined body and a parallel list of segment-start
        # offsets. The joined body matches what the user saw in the
        # split dialog, except we strip leading whitespace consistently
        # below.
        seg_starts = []
        joined = ""
        for k, (_, _, t) in enumerate(para):
            if k > 0:
                joined += " "
            seg_starts.append(len(joined))
            joined += t

        # The dialog's text was `body.strip()` so the user's cursor offset
        # is relative to the stripped string. Apply the leading-strip
        # delta so we index into our `joined` correctly.
        leading_strip = len(joined) - len(joined.lstrip())
        adjusted_offset = offset + leading_strip

        # A split at 0 or at the end is a no-op.
        if adjusted_offset <= 0 or adjusted_offset >= len(joined):
            return

        # Find which segment the offset falls inside (or between).
        split_seg = None
        split_within = 0
        for k in range(len(para)):
            seg_start = seg_starts[k]
            seg_end = seg_start + len(para[k][2])
            if adjusted_offset <= seg_start:
                split_seg = k
                split_within = 0
                break
            if adjusted_offset <= seg_end:
                split_seg = k
                split_within = adjusted_offset - seg_start
                break
        if split_seg is None:
            # Offset past everything; should have been caught above, but
            # defensively bail.
            return

        # Build the two new paragraphs.
        if split_within == 0:
            # Clean break between segments.
            first = list(para[:split_seg])
            second = list(para[split_seg:])
        else:
            # Mid-segment split: cut the segment's text in two. We give
            # both halves the same (start, end) as the original since we
            # don't have word-level timing.
            seg_start_t, seg_end_t, seg_text = para[split_seg]
            text_before = seg_text[:split_within].rstrip()
            text_after = seg_text[split_within:].lstrip()
            first = list(para[:split_seg])
            if text_before:
                first.append((seg_start_t, seg_end_t, text_before))
            second = []
            if text_after:
                second.append((seg_start_t, seg_end_t, text_after))
            second.extend(para[split_seg + 1:])

        if not first or not second:
            # Edge case: split would have produced an empty paragraph.
            return

        # Inherit speaker assignment to both halves; usually the user will
        # then re-assign one half. The alternative (clearing the speaker
        # on the new half) creates more clicks for no gain.
        existing_speaker = self.speakers[idx]

        self.paragraphs[idx] = first
        self.paragraphs.insert(idx + 1, second)
        self.speakers.insert(idx + 1, existing_speaker)

        # Insert a new metadata entry for the new paragraph at idx+1.
        # Estimated height; layout will be recomputed below.
        new_body_full = " ".join(seg[2] for seg in second).strip()
        self.row_widgets.insert(idx + 1, {
            "idx": idx + 1,
            "body_full": new_body_full,
            "y": 0,
            "height": 0,
        })
        self._reindex_rows()
        # The original row's text shrank.
        self._update_row_content(idx)
        # Recompute layout for the new row count and shifts.
        self._compute_layout()
        self._reposition_rendered()
        self._update_visible_rows()
        self._refresh_row(idx)
        self._select_row(idx + 1)

    def _set_speaker(self, letter):
        """Set the selected paragraph's speaker (or clear if letter is None).
        Auto-advances to the next paragraph after assignment."""
        if self.selected_idx is None:
            return
        self.speakers[self.selected_idx] = letter
        self._refresh_row(self.selected_idx)
        # Auto-advance to next unlabelled (or next paragraph).
        nxt = self.selected_idx + 1
        if nxt < len(self.paragraphs):
            self._select_row(nxt)

    def _on_arrow_up(self, event):
        if self._is_text_input_focused():
            return
        if self.selected_idx is None or self.selected_idx == 0:
            return
        self._select_row(self.selected_idx - 1)

    def _on_arrow_down(self, event):
        if self._is_text_input_focused():
            return
        if self.selected_idx is None:
            return
        if self.selected_idx + 1 < len(self.paragraphs):
            self._select_row(self.selected_idx + 1)

    def _merge_with_previous(self):
        """Merge the selected paragraph into its predecessor."""
        i = self.selected_idx
        if i is None or i == 0:
            return
        # Commit any pending edit on either of the rows about to be merged
        # so we don't leave dangling state.
        if self.editing_idx in (i, i - 1):
            self._commit_edit()
        prev = self.paragraphs[i - 1]
        curr = self.paragraphs[i]
        # Update paragraph data: prev gets prev+curr's segments, curr removed.
        self.paragraphs[i - 1] = list(prev) + list(curr)
        del self.paragraphs[i]
        del self.speakers[i]

        # Tear down the removed row if rendered, then drop its metadata.
        self._unrender_row(i)
        self.row_widgets.pop(i)
        # Re-index every row whose position changed (i and onward).
        self._reindex_rows()
        # The merged row's text changed, so its height likely changed too.
        self._update_row_content(i - 1)
        # Recompute layout from scratch — heights of remaining rows are
        # unchanged but their y positions need to shift up by the
        # removed row's height.
        self._compute_layout()
        self._reposition_rendered()
        self._update_visible_rows()

        self.selected_idx = i - 1
        self._refresh_row(i - 1)
        self._select_row(i - 1)

    def _on_name_changed(self, letter):
        self.speaker_names[letter] = self.name_vars[letter].get()

    # ----- Save / cancel ----------------------------------------------------

    def _resolved_speakers(self):
        """Convert the per-paragraph letter assignments to display names.

        If a paragraph has no speaker assigned, its entry stays None.
        """
        return [
            self.speaker_names.get(letter) if letter else None
            for letter in self.speakers
        ]

    def _on_save_clicked(self):
        # Commit any pending in-line edit so the saved transcript reflects
        # the user's latest text.
        if self.editing_idx is not None:
            self._commit_edit()
        # Strip surrounding whitespace from speaker names.
        for letter in self.SPEAKER_LETTERS:
            self.speaker_names[letter] = self.speaker_names[letter].strip()
        speakers = self._resolved_speakers()
        self.on_save_cb(self.paragraphs, speakers)

    def _on_save_revision_clicked(self):
        if self.editing_idx is not None:
            self._commit_edit()
        for letter in self.SPEAKER_LETTERS:
            self.speaker_names[letter] = self.speaker_names[letter].strip()
        speakers = self._resolved_speakers()
        self.on_save_revision_cb(self.paragraphs, speakers)

    def _on_cancel_clicked(self):
        if self.editing_idx is not None:
            self._commit_edit()
        if self.loaded:
            if not messagebox.askyesno(
                "Close without saving?",
                "Close the review pane without saving any changes?\n\n"
                "The original file on disk will not be modified.",
                default="no",
            ):
                return
        self.on_cancel_cb()

    def destroy(self):
        # Cancel any pending after() callbacks on rendered rows so they
        # don't fire on destroyed widgets after we're gone.
        for rw in self.row_widgets:
            pending = rw.pop("_resize_after_id", None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except (tk.TclError, ValueError):
                    pass
        # Clean up the keybindings we made on the toplevel.
        try:
            top = self.winfo_toplevel()
            for letter_idx in range(1, len(self.SPEAKER_LETTERS) + 1):
                top.unbind(str(letter_idx))
            top.unbind("0")
            top.unbind("<Up>")
            top.unbind("<Down>")
            top.unbind("<KeyPress-m>")
            top.unbind("<KeyPress-M>")
            top.unbind("<Return>")
            top.unbind("<F2>")
            self.canvas.unbind("<MouseWheel>")
            self.canvas.unbind("<Button-4>")
            self.canvas.unbind("<Button-5>")
        except tk.TclError:
            pass
        super().destroy()


class ReviewPaneText(ttk.Frame):
    """Single-Text-widget review pane.

    Renders the whole transcript into one tk.Text rather than building
    a Frame per paragraph. Tk's Text widget handles documents of
    arbitrary size without per-line widget overhead, so scrolling is
    instant regardless of paragraph count.

    Shape mirrors ReviewPane so WhisperGUI can swap classes freely.
    """

    # Up to 9 speakers, each with a single-digit hotkey (1-9); 0 clears.
    MAX_SPEAKERS = 9
    # Number of speaker-name fields shown initially. The user can reveal
    # more (up to MAX_SPEAKERS) with the "Add speaker" button, or simply by
    # pressing a higher digit hotkey.
    DEFAULT_VISIBLE = 4
    SPEAKER_LETTERS = [str(i) for i in range(1, MAX_SPEAKERS + 1)]
    DEFAULT_NAMES = {str(i): f"Speaker {i}" for i in range(1, MAX_SPEAKERS + 1)}
    # Subtle pastels, one per speaker, chosen to be mutually distinct and
    # distinct from the selection/editing highlights below.
    SPEAKER_COLOURS = {
        "1": "#fff4d6",   # warm yellow
        "2": "#dfeeff",   # soft blue
        "3": "#e3f4d8",   # soft green
        "4": "#f9d9e7",   # soft pink
        "5": "#e7ddf6",   # lavender
        "6": "#ffe0c2",   # peach
        "7": "#d3f0ee",   # teal
        "8": "#f0e4c8",   # tan
        "9": "#d9e8c0",   # olive
    }

    # Selection / editing colours chosen to be visually distinct from any
    # of the four speaker pastels (which are warm yellow / blue / green /
    # pink). Grey reads as "selected" without competing with speaker bg.
    SELECTED_BG = "#cfcfcf"
    EDITING_BG = "#ffcc80"

    # Pixel positions of the column tab stops. Speaker text occupies the
    # first column (0..SPEAKER_TAB_PX), timestamp the second
    # (SPEAKER_TAB_PX..BODY_TAB_PX), body the third (BODY_TAB_PX..end).
    # Wrapped body lines are indented to BODY_TAB_PX so they align under
    # the body column.
    SPEAKER_TAB_PX = 140
    BODY_TAB_PX = 220

    def __init__(self, parent, paragraphs, *, on_save, on_cancel,
                 show_timestamp=True, loaded=False, on_save_revision=None,
                 word_conf=None, audio_path=None, on_autosave=None):
        super().__init__(parent)
        self.paragraphs = list(paragraphs)
        self.speakers = [None] * len(self.paragraphs)
        self.speaker_names = dict(self.DEFAULT_NAMES)
        # Source audio for paragraph playback. Playback needs both the file
        # and ffplay (ships with ffmpeg, which Whisper already requires);
        # when either is missing the play controls simply don't appear.
        self.audio_path = (str(audio_path)
                           if audio_path and Path(audio_path).exists()
                           else None)
        self._ffplay = shutil.which("ffplay")
        # ffmpeg enables sample-accurate seeking (see _playback_commands);
        # without it playback falls back to ffplay's fast seek.
        self._ffmpeg = shutil.which("ffmpeg")
        self._play_proc = None
        self._decode_proc = None
        self._play_poll_id = None
        # Called (paragraphs, speakers, speaker_names) after mutations so
        # the host can persist a crash-recovery snapshot.
        self.on_autosave_cb = on_autosave
        self._autosave_after_id = None
        # Flat, time-ordered list of (start, end, word, probability) tuples
        # from the engine when word timestamps were captured; None if not
        # available (e.g. loaded transcripts). Used to shade low-confidence
        # words. See _words_for_paragraph / _shade_low_confidence.
        self.word_conf = list(word_conf) if word_conf else None
        # How many speaker-name fields are currently shown. Grows on demand.
        self.visible_speakers = self.DEFAULT_VISIBLE
        self.show_timestamp = show_timestamp
        self.on_save_cb = on_save
        self.on_cancel_cb = on_cancel
        self.on_save_revision_cb = on_save_revision
        self.loaded = loaded
        self.selected_idx = 0 if self.paragraphs else None
        # When non-None, the index of the paragraph currently being edited.
        # Edit mode unlocks typing inside that paragraph's body range.
        self.editing_idx = None
        self._edit_original = None  # snapshot of body text for cancel

        # Undo/redo stacks of state snapshots (see _snapshot).
        self._undo_stack = []
        self._redo_stack = []
        self._UNDO_LIMIT = 200
        # Whether low-confidence shading is currently on. Only meaningful
        # when word_conf is available.
        self.show_confidence = bool(self.word_conf)
        # Search cursor (a Text index) for repeated Find-next.
        self._search_from = "1.0"

        self._build_ui()
        self._render_all()
        if self.selected_idx is not None:
            self._update_highlight(scroll_into_view=True)
        # Defer focus to after the pane is laid out; focus_set on a not-
        # yet-visible widget silently fails.
        self.after_idle(self._take_focus)

    def _take_focus(self):
        try:
            self.text.focus_set()
        except tk.TclError:
            pass

    # ----- UI construction -------------------------------------------------

    def _build_ui(self):
        # Header: title on the left, live labelling progress on the right.
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(
            header, text="Review and label speakers",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(side="left")
        self.header_count_var = tk.StringVar()
        ttk.Label(
            header, textvariable=self.header_count_var,
            foreground="gray",
        ).pack(side="right")
        self._update_label_counter()

        # Speaker name editor. Built dynamically so the user can reveal
        # additional speakers on demand (up to MAX_SPEAKERS).
        self.names_frame = ttk.LabelFrame(self, text="Speaker names", padding=6)
        self.names_frame.pack(fill="x", padx=10, pady=4)
        self.name_vars = {}
        self._build_names_editor()

        # Editing toolbar: undo/redo + find & replace + (when available)
        # the low-confidence shading toggle.
        self._build_toolbar()

        # The big Text + scrollbar
        body_frame = ttk.LabelFrame(self, text="Transcript", padding=4)
        body_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.text = tk.Text(
            body_frame, wrap="word",
            font=("TkDefaultFont", 11),
            padx=10, pady=10,
            cursor="arrow",
            highlightthickness=0,
            relief="flat",
            spacing3=0,
            takefocus=True,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            body_frame, orient="vertical", command=self.text.yview)
        scrollbar.pack(side="right", fill="y")
        self.text.config(yscrollcommand=scrollbar.set)

        # The "row" tag carries paragraph-level layout: tab stops define
        # the speaker / timestamp / body columns, and lmargin2 makes
        # wrapped body lines align under the body column. Applied to
        # every line of every paragraph so tabs and wrapping behave
        # consistently. Inline tags (speaker_text, timestamp, body_text)
        # add font/colour styling to specific portions.
        self.text.tag_configure(
            "row",
            tabs=(str(self.SPEAKER_TAB_PX), str(self.BODY_TAB_PX)),
            tabstyle="tabular",
            lmargin1=0, lmargin2=self.BODY_TAB_PX,
            spacing3=4,
        )
        self.text.tag_configure(
            "speaker_text",
            font=("TkDefaultFont", 10, "bold"),
        )
        self.text.tag_configure("timestamp", font=("Courier", 9))
        self.text.tag_configure("body_text")
        # Colours for the widget itself and every tag come from the active
        # light/dark palette; _configure_tags is re-run on theme switches.
        self._configure_tags()
        # Tag priority: search > editing > selected > confidence > speaker bg.
        self.text.tag_raise("conf_med")
        self.text.tag_raise("conf_low")
        self.text.tag_raise("selected")
        self.text.tag_raise("editing")
        self.text.tag_raise("search")

        # Help line
        ttk.Label(
            self,
            text=(
                "Click a paragraph to select  ·  Up/Down navigate  ·  "
                "1-9 set speaker  ·  0 clear  ·  M merge with previous  ·  "
                "Double-click a word to split  ·  Enter to edit text  ·  "
                "N next unlabelled  ·  P play audio  ·  "
                "Ctrl+Z undo / Ctrl+Shift+Z redo  ·  Ctrl+F find"
            ),
            foreground="gray",
        ).pack(fill="x", padx=10, pady=(2, 4))

        # Action buttons. "Accent.TButton" is sv-ttk's highlighted style
        # for the primary action; without sv-ttk it falls back to the
        # normal button look.
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(4, 10))
        if self.loaded:
            ttk.Button(actions, text="Save (overwrite original)",
                       style="Accent.TButton",
                       command=self._on_save_clicked).pack(side="left")
            if self.on_save_revision_cb is not None:
                ttk.Button(actions, text="Save as revision...",
                           command=self._on_save_revision_clicked
                           ).pack(side="left", padx=(8, 0))
            ttk.Button(actions, text="Close without saving",
                       command=self._on_cancel_clicked).pack(side="right")
        else:
            ttk.Button(actions, text="Save with labels",
                       style="Accent.TButton",
                       command=self._on_save_clicked).pack(side="left")
            ttk.Button(actions, text="Save without labels",
                       command=self._on_cancel_clicked).pack(side="right")

        # Bindings on the Text widget. We dispatch hotkeys (1-9, M, etc)
        # from inside _on_text_key rather than via toplevel bindings,
        # because Tk's binding-tag chain is widget -> class -> toplevel
        # and a "break" return from the widget binding (needed to stop
        # the class binding from inserting text) also stops toplevel
        # bindings. So the widget binding has to handle them itself.
        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<Double-Button-1>", self._on_double_click)
        self.text.bind("<Key>", self._on_text_key)
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<<Paste>>", self._on_text_paste_or_cut)
        self.text.bind("<<Cut>>", self._on_text_paste_or_cut)
        # Undo/redo and find. Bind both Control (Win/Linux) and Command
        # (macOS) so the natural shortcut works on every platform.
        for seq in ("<Control-z>", "<Command-z>"):
            self.text.bind(seq, self._undo)
        for seq in ("<Control-y>", "<Command-y>",
                    "<Control-Shift-Z>", "<Command-Shift-Z>",
                    "<Control-Shift-z>", "<Command-Shift-z>"):
            self.text.bind(seq, self._redo)
        for seq in ("<Control-f>", "<Command-f>"):
            self.text.bind(seq, self._focus_find)

    def _build_toolbar(self):
        """Undo/redo + find & replace controls, shown above the transcript."""
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(0, 2))

        self.undo_btn = ttk.Button(bar, text="Undo", width=6,
                                   command=self._undo, state="disabled")
        self.undo_btn.pack(side="left")
        self.redo_btn = ttk.Button(bar, text="Redo", width=6,
                                   command=self._redo, state="disabled")
        self.redo_btn.pack(side="left", padx=(4, 0))

        # Paragraph audio playback, when we have the source audio and
        # ffplay on PATH.
        if self.audio_path and self._ffplay:
            self.play_btn = ttk.Button(bar, text="▶ Play", width=8,
                                       command=self._toggle_play)
            self.play_btn.pack(side="left", padx=(12, 0))

        ttk.Separator(bar, orient="vertical").pack(
            side="left", fill="y", padx=8)

        ttk.Label(bar, text="Find:").pack(side="left")
        self.find_var = tk.StringVar()
        self.find_var.trace_add("write", lambda *_: self._reset_search())
        find_entry = ttk.Entry(bar, textvariable=self.find_var, width=16)
        find_entry.pack(side="left", padx=(4, 6))
        find_entry.bind("<Return>", self._find_next)
        self._find_entry = find_entry

        ttk.Label(bar, text="Replace:").pack(side="left")
        self.replace_var = tk.StringVar()
        replace_entry = ttk.Entry(bar, textvariable=self.replace_var, width=16)
        replace_entry.pack(side="left", padx=(4, 6))
        replace_entry.bind("<Return>", lambda _e: self._replace_all())

        ttk.Button(bar, text="Find next",
                   command=self._find_next).pack(side="left")
        ttk.Button(bar, text="Replace all",
                   command=self._replace_all).pack(side="left", padx=(4, 0))

        self.find_case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Match case",
                        variable=self.find_case_var,
                        command=self._reset_search).pack(side="left", padx=(8, 0))

        self.find_status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.find_status_var,
                  foreground="gray").pack(side="left", padx=(8, 0))

        # Low-confidence shading toggle, only meaningful when we have
        # per-word confidence data.
        if self.word_conf:
            self.confidence_toggle_var = tk.BooleanVar(value=self.show_confidence)
            ttk.Checkbutton(
                bar, text="Shade low-confidence words",
                variable=self.confidence_toggle_var,
                command=self._toggle_confidence,
            ).pack(side="right")

    def _configure_tags(self):
        """Apply the active light/dark palette to the Text widget and every
        colour-bearing tag. Safe to re-run on a live widget, which is how a
        theme switch re-skins the transcript in place."""
        pal = _palette()
        self.text.config(
            background=pal["text_bg"], foreground=pal["text_fg"],
            insertbackground=pal["insert"],
        )
        self.text.tag_configure("speaker_text", foreground=pal["speaker_fg"])
        self.text.tag_configure("timestamp", foreground=pal["timestamp_fg"])
        # Low-confidence word shading (two tiers). Configured always; only
        # applied when word_conf is present and shading is enabled.
        self.text.tag_configure("conf_low", background=pal["conf_low"])
        self.text.tag_configure("conf_med", background=pal["conf_med"])
        self.text.tag_configure("selected", background=pal["selected_bg"])
        self.text.tag_configure("editing", background=pal["editing_bg"])
        # Find-next highlight.
        self.text.tag_configure("search", background=pal["search_bg"])
        for letter, colour in pal["speaker_colours"].items():
            self.text.tag_configure(f"sbg_{letter}", background=colour)

    def apply_palette(self):
        """Re-skin the pane after a theme switch: transcript colours and
        the speaker badges (which bake colours in at build time)."""
        self._configure_tags()
        self._build_names_editor()

    def _update_label_counter(self):
        labelled = sum(1 for s in self.speakers if s)
        self.header_count_var.set(
            f"{labelled} of {len(self.paragraphs)} paragraphs labelled")

    def _build_names_editor(self):
        """(Re)populate the speaker-name editor with one labelled entry per
        visible speaker, plus an 'Add speaker' button while there's room for
        more. Called on construction and whenever visible_speakers grows."""
        pal = _palette()
        for child in self.names_frame.winfo_children():
            child.destroy()
        self.name_vars = {}
        n = max(1, min(self.visible_speakers, self.MAX_SPEAKERS))
        for col in range(n):
            letter = self.SPEAKER_LETTERS[col]
            sub = ttk.Frame(self.names_frame)
            sub.grid(row=col // 2, column=col % 2, sticky="ew", padx=4, pady=2)
            self.names_frame.columnconfigure(col % 2, weight=1)
            badge = tk.Label(
                sub, text=letter, width=2,
                font=("TkDefaultFont", 10, "bold"),
                bg=pal["speaker_colours"][letter], fg=pal["badge_fg"],
                relief="solid", borderwidth=1,
            )
            badge.pack(side="left", padx=(0, 6))
            var = tk.StringVar(value=self.speaker_names[letter])
            self.name_vars[letter] = var
            entry = ttk.Entry(sub, textvariable=var)
            entry.pack(side="left", fill="x", expand=True)
            var.trace_add("write", lambda *_, L=letter: self._on_name_changed(L))

        # "Add speaker" button sits in the next grid cell while there's
        # still an unused speaker slot to reveal.
        if n < self.MAX_SPEAKERS:
            btn = ttk.Button(
                self.names_frame, text="+ Add speaker",
                command=self._on_add_speaker,
            )
            btn.grid(row=n // 2, column=n % 2, sticky="w", padx=4, pady=2)

    def _on_add_speaker(self):
        if self.visible_speakers < self.MAX_SPEAKERS:
            self.visible_speakers += 1
            self._build_names_editor()
            # Return focus to the transcript so hotkeys keep working.
            self.after_idle(self._take_focus)

    def set_visible_speakers(self, n):
        """Ensure at least `n` speaker-name fields are shown (clamped to
        MAX_SPEAKERS). Used when loading a transcript that already has more
        than the default number of distinct speakers."""
        n = max(self.DEFAULT_VISIBLE, min(int(n), self.MAX_SPEAKERS))
        if n != self.visible_speakers:
            self.visible_speakers = n
            self._build_names_editor()

    # ----- Undo / redo -----------------------------------------------------

    def _snapshot(self):
        """A cheap, restorable snapshot of all mutable document state.
        Paragraph segments are immutable tuples, so copying the list-of-lists
        one level deep is sufficient."""
        return {
            "paragraphs": [list(p) for p in self.paragraphs],
            "speakers": list(self.speakers),
            "speaker_names": dict(self.speaker_names),
            "visible_speakers": self.visible_speakers,
            "selected_idx": self.selected_idx,
        }

    def _push_undo(self):
        """Record the current state for undo and clear the redo stack.
        Call this immediately before any mutating operation."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_buttons()
        # Every mutation passes through here, so it doubles as the
        # autosave trigger.
        self._schedule_autosave()

    def _schedule_autosave(self):
        """Debounced crash-recovery snapshot: fires 3s after the last
        mutation rather than on every keystroke of a rename."""
        if self.on_autosave_cb is None:
            return
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except (tk.TclError, ValueError):
                pass
        self._autosave_after_id = self.after(3000, self._do_autosave)

    def _do_autosave(self):
        self._autosave_after_id = None
        if self.on_autosave_cb is not None:
            self.on_autosave_cb(self.paragraphs, self.speakers,
                                dict(self.speaker_names))

    def _restore(self, snap):
        self.paragraphs = [list(p) for p in snap["paragraphs"]]
        self.speakers = list(snap["speakers"])
        self.speaker_names = dict(snap["speaker_names"])
        self.visible_speakers = snap["visible_speakers"]
        self.selected_idx = snap["selected_idx"]
        if self.selected_idx is not None and self.paragraphs:
            self.selected_idx = max(0, min(self.selected_idx,
                                           len(self.paragraphs) - 1))
        elif not self.paragraphs:
            self.selected_idx = None
        # Rebuilds the editor with name_vars initialised from speaker_names.
        self._build_names_editor()
        self._render_all()
        self._update_highlight(scroll_into_view=True)

    def _undo(self, event=None):
        # Don't undo mid-edit; require the user to commit/cancel first.
        if self.editing_idx is not None or not self._undo_stack:
            return "break"
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._update_undo_buttons()
        self._schedule_autosave()
        return "break"

    def _redo(self, event=None):
        if self.editing_idx is not None or not self._redo_stack:
            return "break"
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._update_undo_buttons()
        self._schedule_autosave()
        return "break"

    def _update_undo_buttons(self):
        if not hasattr(self, "undo_btn"):
            return
        self.undo_btn.config(
            state="normal" if self._undo_stack else "disabled")
        self.redo_btn.config(
            state="normal" if self._redo_stack else "disabled")

    # ----- Find & replace --------------------------------------------------

    def _focus_find(self, event=None):
        try:
            self._find_entry.focus_set()
            self._find_entry.selection_range(0, "end")
        except (tk.TclError, AttributeError):
            pass
        return "break"

    def _reset_search(self, *_):
        """Forget where Find-next was up to (the term or case option
        changed) and clear any existing match highlight."""
        self._search_from = "1.0"
        try:
            self.text.tag_remove("search", "1.0", "end")
        except tk.TclError:
            pass
        self.find_status_var.set("")

    def _find_next(self, event=None):
        term = self.find_var.get()
        if not term:
            return "break"
        nocase = 0 if self.find_case_var.get() else 1
        self.text.tag_remove("search", "1.0", "end")
        countv = tk.IntVar()
        idx = self.text.search(term, self._search_from, stopindex="end",
                               nocase=nocase, count=countv)
        wrapped = False
        if not idx:
            idx = self.text.search(term, "1.0", stopindex="end",
                                   nocase=nocase, count=countv)
            wrapped = True
        if not idx:
            self.find_status_var.set("Not found")
            return "break"
        end = f"{idx}+{countv.get()}c"
        self.text.tag_add("search", idx, end)
        self.text.see(idx)
        self._search_from = end
        self.find_status_var.set("Wrapped" if wrapped else "")
        return "break"

    def _replace_all(self):
        term = self.find_var.get()
        if not term:
            return
        repl = self.replace_var.get()
        flags = 0 if self.find_case_var.get() else re.IGNORECASE
        pattern = re.compile(re.escape(term), flags)

        # Count first so we can avoid a pointless snapshot/re-render.
        total = sum(pattern.subn(lambda _m: repl, seg[2])[1]
                    for para in self.paragraphs for seg in para)
        if total == 0:
            self.find_status_var.set("No matches")
            return

        self._push_undo()
        for para in self.paragraphs:
            for k, seg in enumerate(para):
                new_text, n = pattern.subn(lambda _m: repl, seg[2])
                if n:
                    para[k] = (seg[0], seg[1], new_text)
        self._reset_search()
        self._render_all()
        self._update_highlight(scroll_into_view=False)
        self.find_status_var.set(f"Replaced {total}")

    # ----- Confidence shading ----------------------------------------------

    def _toggle_confidence(self):
        self.show_confidence = bool(self.confidence_toggle_var.get())
        self._render_all()
        self._update_highlight(scroll_into_view=False)

    # ----- Audio playback ----------------------------------------------------

    def _can_play(self):
        return bool(self.audio_path and self._ffplay)

    def _playback_span(self, idx):
        """Return (start_seconds, duration_seconds_or_None) covering
        paragraph `idx` for playback, or None if there's nothing to play.
        A None duration means "play to the end of the file".

        Fresh transcriptions carry every Whisper segment, so the last
        segment's end time is the paragraph's real end. Loaded transcripts
        only know start times (the parsers synthesise ~1s spans, stretched
        to the next paragraph's start by _infer_paragraph_end_times), so
        if a span still looks like a placeholder we fall back to the next
        paragraph's start - or play open-ended for the last paragraph."""
        if not (0 <= idx < len(self.paragraphs)):
            return None
        para = self.paragraphs[idx]
        if not para:
            return None
        start = max(0.0, float(para[0][0]))
        end = float(para[-1][1])
        if end - start <= 1.0:
            if idx + 1 < len(self.paragraphs) and self.paragraphs[idx + 1]:
                next_start = float(self.paragraphs[idx + 1][0][0])
                if next_start > start:
                    return (start, max(0.5, next_start - start + 0.3))
            return (start, None)
        # A touch of tail padding so the last word isn't clipped.
        return (start, max(0.5, end - start + 0.3))

    def _playback_commands(self, start, dur):
        """Build the player command line(s) for the span. Returns
        (decode_cmd_or_None, play_cmd).

        ffplay's own -ss does a *fast* seek: it jumps to the nearest seek
        point at or before the target, which on formats with sparse seek
        points (VBR mp3 in particular) can land seconds early - audio from
        the preceding paragraphs plays first. When ffmpeg is available we
        let it do the seeking instead: input-side -ss decodes from the
        prior seek point and discards samples up to the exact timestamp,
        then streams WAV into ffplay. Without ffmpeg, fall back to the
        fast (possibly early) direct seek."""
        if self._ffmpeg:
            decode = [self._ffmpeg, "-hide_banner", "-loglevel", "error",
                      "-ss", f"{start:.2f}", "-i", self.audio_path]
            if dur is not None:
                decode += ["-t", f"{dur:.2f}"]
            decode += ["-f", "wav", "pipe:1"]
            play = [self._ffplay, "-nodisp", "-autoexit",
                    "-loglevel", "quiet", "-i", "pipe:0"]
            return decode, play

        play = [self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
                "-ss", f"{start:.2f}"]
        if dur is not None:
            play += ["-t", f"{dur:.2f}"]
        play.append(self.audio_path)
        return None, play

    def _toggle_play(self):
        """Play the selected paragraph's audio span; press again to stop."""
        if self._play_proc is not None:
            self._stop_playback()
            return
        if not self._can_play() or self.selected_idx is None:
            return
        span = self._playback_span(self.selected_idx)
        if span is None:
            return
        start, dur = span
        decode_cmd, play_cmd = self._playback_commands(start, dur)
        try:
            if decode_cmd is not None:
                self._decode_proc = subprocess.Popen(
                    decode_cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                self._play_proc = subprocess.Popen(
                    play_cmd,
                    stdin=self._decode_proc.stdout,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # Drop our handle so ffmpeg sees the pipe close (and stops
                # decoding) as soon as the player exits.
                self._decode_proc.stdout.close()
            else:
                self._play_proc = subprocess.Popen(
                    play_cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except OSError as e:
            _log(f"audio playback failed to start: {e}")
            self._reap_playback_procs()
            return
        self.play_btn.config(text="■ Stop")
        self._poll_playback()

    def _reap_playback_procs(self):
        """Terminate (best-effort) and forget both playback processes."""
        for attr in ("_play_proc", "_decode_proc"):
            proc = getattr(self, attr, None)
            setattr(self, attr, None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass

    def _poll_playback(self):
        """Reset the Play button once the player exits on its own."""
        proc = self._play_proc
        if proc is None:
            return
        if proc.poll() is None:
            self._play_poll_id = self.after(200, self._poll_playback)
            return
        self._play_poll_id = None
        self._reap_playback_procs()
        try:
            self.play_btn.config(text="▶ Play")
        except tk.TclError:
            pass

    def _stop_playback(self):
        if self._play_poll_id is not None:
            try:
                self.after_cancel(self._play_poll_id)
            except (tk.TclError, ValueError):
                pass
            self._play_poll_id = None
        self._reap_playback_procs()
        try:
            self.play_btn.config(text="▶ Play")
        except (tk.TclError, AttributeError):
            pass

    def _bucket_words_by_paragraph(self):
        """Partition the flat word_conf list into one list of (word, prob)
        per paragraph, using each paragraph's end time as the boundary.
        Both paragraphs and words are time-ordered, so a single forward
        pass suffices. Robust to edits/splits/merges because paragraph
        times stay monotonic."""
        buckets = [[] for _ in self.paragraphs]
        words = self.word_conf or []
        nW = len(words)
        wi = 0
        n_para = len(self.paragraphs)
        for i, para in enumerate(self.paragraphs):
            if not para:
                continue
            p_end = para[-1][1]
            is_last = (i == n_para - 1)
            while wi < nW:
                w_start = words[wi][0]
                if not is_last and w_start >= p_end:
                    break
                buckets[i].append((words[wi][2], words[wi][3]))
                wi += 1
        return buckets

    def _time_at_body_offset(self, para, body, offset):
        """Best-effort: the start time of the word at/after character
        `offset` in `body` (the joined text of `para`), from the engine's
        word timestamps. Returns None when word data is missing or no
        longer aligns with the (possibly edited) text."""
        if not self.word_conf or not para:
            return None
        p_start = para[0][0]
        p_end = para[-1][1]
        cursor = 0
        for w_start, _w_end, w_text, _prob in self.word_conf:
            # Only words inside this paragraph's time span are relevant.
            if w_start < p_start - 0.001:
                continue
            if w_start > p_end + 0.001:
                break
            token = (w_text or "").strip()
            if not token:
                continue
            pos = body.find(token, cursor)
            if pos < 0:
                return None  # alignment lost (paragraph was edited)
            if pos >= offset:
                return float(w_start)
            cursor = pos + len(token)
        return None

    def _shade_paragraph(self, i, body, words_i):
        """Tag low-confidence words within paragraph i's rendered body.

        Walks the paragraph's words in order, locating each in the body
        string from a moving cursor (so repeated words match the right
        occurrence). On any alignment drift - e.g. the paragraph was edited
        so its text no longer matches the captured words - we bail out and
        leave the paragraph unshaded rather than mis-highlight."""
        if not words_i:
            return
        base = f"txt_{i}_start"
        cursor = 0
        for wtext, prob in words_i:
            token = (wtext or "").strip()
            if not token:
                continue
            pos = body.find(token, cursor)
            if pos < 0:
                return  # alignment lost; skip the rest of this paragraph
            cursor = pos + len(token)
            if prob is None:
                continue
            if prob < 0.35:
                tag = "conf_low"
            elif prob < 0.6:
                tag = "conf_med"
            else:
                continue
            try:
                self.text.tag_add(
                    tag, f"{base} +{pos}c", f"{base} +{cursor}c")
            except tk.TclError:
                return

    # ----- Rendering -------------------------------------------------------

    def _render_all(self, *, anchor_idx=None):
        """Rebuild the Text from self.paragraphs/speakers. Each paragraph
        is rendered as one logical line in three tab-aligned columns:
        speaker / timestamp / body. Body wraps inside its column.

        If `anchor_idx` is given and that paragraph was visible before
        the re-render, the viewport is scrolled afterwards so the
        paragraph stays at the same screen y-position. Used for
        speaker-change / split / merge / commit-edit, where the user is
        focused on a specific paragraph and a layout jump would be
        disorienting."""
        # Capture the anchor paragraph's pre-render screen position.
        anchor_y_before = None
        if (anchor_idx is not None
                and 0 <= anchor_idx < len(self.paragraphs)):
            try:
                bbox = self.text.bbox(f"blk_{anchor_idx}_start")
                if bbox:
                    anchor_y_before = bbox[1]
            except tk.TclError:
                pass
        first, _last = self.text.yview()

        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for m in list(self.text.mark_names()):
            if m.startswith(("blk_", "txt_")):
                self.text.mark_unset(m)

        # Per-paragraph word lists for low-confidence shading, computed once.
        para_words = (self._bucket_words_by_paragraph()
                      if (self.show_confidence and self.word_conf) else None)

        last_speaker = None
        for i, para in enumerate(self.paragraphs):
            self.text.mark_set(f"blk_{i}_start", "end-1c")
            self.text.mark_gravity(f"blk_{i}_start", "left")

            speaker = self.speakers[i]
            # Determine what to show in the speaker column. Like the
            # current writer, we only emit a label when the speaker
            # changes - successive paragraphs by the same speaker have a
            # blank speaker column. The change of speaker to None after
            # a named speaker emits the explicit unattributed marker.
            if speaker is not None and speaker != last_speaker:
                label_text = self.speaker_names.get(
                    speaker, f"Speaker {speaker}")
            elif speaker is None and last_speaker is not None:
                label_text = UNATTRIBUTED_LABEL
            else:
                label_text = ""
            last_speaker = speaker

            # Speaker column.
            self.text.insert("end", label_text, ("row", "speaker_text"))
            self.text.insert("end", "\t", ("row",))
            # Timestamp column.
            ts = (format_timestamp(para[0][0])
                  if (self.show_timestamp and para) else "")
            self.text.insert("end", ts, ("row", "timestamp"))
            self.text.insert("end", "\t", ("row",))

            # Body column. txt_start marks the first character of the
            # body so we can locate it later for edits / splits.
            self.text.mark_set(f"txt_{i}_start", "end-1c")
            self.text.mark_gravity(f"txt_{i}_start", "left")

            body_text = " ".join(seg[2] for seg in para).strip()
            self.text.insert("end", body_text, ("row", "body_text"))

            # Shade low-confidence words within the body (if enabled).
            if para_words is not None:
                self._shade_paragraph(i, body_text, para_words[i])

            # Trailing newline ends the row's last visual line. It carries
            # the row tag so the speaker bg (added below) fills the line
            # all the way to the right margin.
            self.text.insert("end", "\n", ("row",))
            self.text.mark_set(f"blk_{i}_end", "end-1c")
            self.text.mark_gravity(f"blk_{i}_end", "left")

            if speaker is not None:
                self.text.tag_add(
                    f"sbg_{speaker}",
                    f"blk_{i}_start", f"blk_{i}_end")

            # Untagged blank line as paragraph separator.
            self.text.insert("end", "\n")

        self.text.config(state="disabled")

        # Restore scroll position. If we have an anchor that was visible
        # before, scroll so it lands at the same screen y-pixel after
        # re-render. Otherwise restore the fractional yview from before.
        if (anchor_y_before is not None
                and anchor_idx is not None
                and 0 <= anchor_idx < len(self.paragraphs)):
            self._scroll_anchor_to(f"blk_{anchor_idx}_start", anchor_y_before)
        else:
            try:
                self.text.yview_moveto(first)
            except tk.TclError:
                pass

        self._update_label_counter()
        self._update_highlight(scroll_into_view=False)

    def _scroll_anchor_to(self, index, target_y):
        """Scroll the Text so that `index` is at viewport y-pixel
        `target_y`. Iteratively converges using pixel scroll (preferred)
        or line scroll (fallback for older Tk). Stops within ~2px or
        after a few iterations."""
        try:
            # Make sure the index is visible so bbox returns a value.
            self.text.see(index)
            self.text.update_idletasks()
        except tk.TclError:
            return

        for _ in range(8):
            try:
                bbox = self.text.bbox(index)
            except tk.TclError:
                return
            if not bbox:
                return
            delta = bbox[1] - target_y
            if abs(delta) <= 1:
                return
            # Try pixel-precise scrolling. Tk 8.5+ Text supports
            # "pixels" as a yview-scroll unit, but we fall back to lines
            # if not.
            try:
                self.text.yview_scroll(int(delta), "pixels")
            except tk.TclError:
                line_h = bbox[3] or 16
                step = max(1, int(round(abs(delta) / line_h)))
                self.text.yview_scroll(
                    step if delta > 0 else -step, "units")
            try:
                self.text.update_idletasks()
            except tk.TclError:
                return

    def _update_highlight(self, *, scroll_into_view):
        """Reapply the selected/editing background to the right paragraph."""
        self.text.tag_remove("selected", "1.0", "end")
        self.text.tag_remove("editing", "1.0", "end")
        idx = self.editing_idx if self.editing_idx is not None else self.selected_idx
        if idx is None:
            return
        try:
            tag = "editing" if self.editing_idx is not None else "selected"
            self.text.tag_add(tag, f"blk_{idx}_start", f"blk_{idx}_end")
            if scroll_into_view:
                self.text.see(f"blk_{idx}_start")
        except tk.TclError:
            pass

    # ----- Paragraph lookup ------------------------------------------------

    def _para_at_index(self, text_index):
        """Return the paragraph idx whose block contains `text_index`, or None."""
        try:
            target_line = int(self.text.index(text_index).split(".")[0])
        except (tk.TclError, ValueError):
            return None
        # Find the highest i with blk_i_start.line <= target_line.
        result = None
        for i in range(len(self.paragraphs)):
            try:
                start_line = int(
                    self.text.index(f"blk_{i}_start").split(".")[0])
            except tk.TclError:
                continue
            if start_line > target_line:
                break
            result = i
        return result

    # ----- Mouse events ----------------------------------------------------

    def _on_click(self, event):
        clicked_idx = self.text.index(f"@{event.x},{event.y}")
        para = self._para_at_index(clicked_idx)
        if para is None:
            return
        if self.editing_idx is not None and self.editing_idx != para:
            self._commit_edit()
        if para != self.selected_idx:
            self.selected_idx = para
            self._update_highlight(scroll_into_view=False)
        self.text.focus_set()
        # If we're still in edit mode (clicked within the editing
        # paragraph), ensure the cursor stays inside the body range
        # rather than landing on the speaker / timestamp columns. We
        # clamp after_idle so Tk's class binding has placed the cursor
        # first - we're correcting it.
        if self.editing_idx is not None:
            self.after_idle(self._clamp_cursor_to_body)
        # Don't return "break" - let Tk position the cursor where clicked.

    def _on_double_click(self, event):
        # In edit mode, let Tk's default word-select happen.
        if self.editing_idx is not None:
            return None
        clicked_idx = self.text.index(f"@{event.x},{event.y}")
        para = self._para_at_index(clicked_idx)
        if para is None:
            return "break"
        # Compute character offset within the body text (after timestamp).
        try:
            txt_start = self.text.index(f"txt_{para}_start")
            offset = len(self.text.get(txt_start, clicked_idx))
        except tk.TclError:
            return "break"
        body_full = self.text.get(
            self.text.index(f"txt_{para}_start"),
            self.text.index(f"txt_{para}_start lineend"))
        if not body_full or offset <= 0 or offset >= len(body_full):
            return "break"
        # Walk back to start of word (same word-char rules as the row pane).
        def is_word_char(ch):
            return ch.isalnum() or ch in ("'", "’", "-")
        start = offset
        while start > 0 and is_word_char(body_full[start - 1]):
            start -= 1
        if start <= 0:
            return "break"
        self._do_split(para, start, body_full)
        return "break"

    # ----- Keyboard --------------------------------------------------------

    def _on_text_key(self, event):
        # In edit mode: allow most keys but constrain edits to the body
        # range. Special keys (Escape cancels; Enter / F2 commit).
        if self.editing_idx is not None:
            ks = event.keysym
            if ks == "Escape":
                self._cancel_edit()
                return "break"
            if ks in ("Return", "F2"):
                self._commit_edit()
                return "break"
            return self._maybe_constrain_edit(event)

        # Read-only mode: dispatch hotkeys, then block anything that
        # would otherwise modify the text.
        ks = event.keysym
        if ks in self.SPEAKER_LETTERS:  # "1".."9"
            self._kb_set_speaker(ks)
            return "break"
        if ks == "0":
            self._kb_set_speaker(None)
            return "break"
        if ks in ("m", "M"):
            self._kb_merge()
            return "break"
        if ks in ("n", "N"):
            self._jump_next_attention()
            return "break"
        if ks in ("p", "P"):
            if self._can_play():
                self._toggle_play()
            return "break"
        if ks in ("Return", "F2"):
            self._kb_edit()
            return "break"
        if ks == "Escape":
            return "break"
        if ks == "Up":
            self._on_arrow_up(event)
            return "break"
        if ks == "Down":
            self._on_arrow_down(event)
            return "break"
        # Other navigation keys pass through to Tk's class binding.
        if ks in ("Prior", "Next", "Home", "End", "Left", "Right"):
            return None
        # Block everything that would modify the text.
        if ks in ("BackSpace", "Delete", "Tab"):
            return "break"
        if event.char and event.char.isprintable():
            return "break"
        return None

    def _on_text_paste_or_cut(self, event):
        if self.editing_idx is None:
            return "break"
        return None

    def _maybe_constrain_edit(self, event):
        """In edit mode, block edits that would reach across the body's
        boundaries. We use lineend dynamically (rather than a left-gravity
        end-mark) so the boundary moves with characters typed at the end -
        otherwise typing at end snaps the cursor back to the original end
        position and types appear in reverse order."""
        idx = self.editing_idx
        try:
            txt_start = self.text.index(f"txt_{idx}_start")
            line_end = self.text.index(f"{txt_start} lineend")
        except tk.TclError:
            return None
        if event.keysym == "BackSpace":
            if self.text.compare("insert", "<=", txt_start):
                return "break"
        if event.keysym == "Delete":
            if self.text.compare("insert", ">=", line_end):
                return "break"
        return None

    def _on_key_release(self, event):
        """After any key in edit mode, clamp the cursor back into the
        body range. Catches arrow keys, Home/End, Page Up/Down etc that
        would otherwise let the cursor wander into the speaker / timestamp
        columns or into adjacent paragraphs."""
        if self.editing_idx is not None:
            self._clamp_cursor_to_body()

    def _clamp_cursor_to_body(self):
        """Snap the insert cursor back inside the editing paragraph's
        body range if it's drifted out (via arrow key, click, etc.)."""
        idx = self.editing_idx
        if idx is None:
            return
        try:
            txt_start = self.text.index(f"txt_{idx}_start")
            line_end = self.text.index(f"{txt_start} lineend")
            if self.text.compare("insert", "<", txt_start):
                self.text.mark_set("insert", txt_start)
            elif self.text.compare("insert", ">", line_end):
                self.text.mark_set("insert", line_end)
        except tk.TclError:
            pass

    def _is_text_input_focused(self):
        """Hotkey suppression: True if a speaker-name Entry has focus.
        The main transcript Text doesn't suppress hotkeys (we only
        suppress them in edit mode, which callers check separately)."""
        try:
            focused = self.winfo_toplevel().focus_get()
        except (KeyError, tk.TclError):
            return False
        if focused is None or focused is self.text:
            return False
        cls = focused.winfo_class()
        return cls in ("Entry", "TEntry", "Text",
                       "TCombobox", "Spinbox", "TSpinbox")

    def _kb_set_speaker(self, letter):
        if self._is_text_input_focused() or self.editing_idx is not None:
            return
        if self.selected_idx is None:
            return
        self._push_undo()
        anchor = self.selected_idx
        self.speakers[self.selected_idx] = letter
        # If the user assigned a speaker whose name field isn't visible yet
        # (e.g. pressed "6" while only 4 were shown), reveal it.
        if letter is not None:
            slot = int(letter)
            if slot > self.visible_speakers:
                self.set_visible_speakers(slot)
        # Auto-advance to next paragraph.
        if self.selected_idx + 1 < len(self.paragraphs):
            self.selected_idx += 1
        # Anchor on the just-modified paragraph so the page doesn't jump.
        self._render_all(anchor_idx=anchor)
        # Don't scroll-into-view: the user was looking at this paragraph.
        self._update_highlight(scroll_into_view=False)

    def _kb_merge(self):
        if self._is_text_input_focused() or self.editing_idx is not None:
            return
        i = self.selected_idx
        if i is None or i == 0:
            return
        self._push_undo()
        self.paragraphs[i - 1] = list(self.paragraphs[i - 1]) + list(self.paragraphs[i])
        del self.paragraphs[i]
        del self.speakers[i]
        self.selected_idx = i - 1
        # Anchor on the merged paragraph so the page doesn't jump.
        self._render_all(anchor_idx=i - 1)
        self._update_highlight(scroll_into_view=False)

    def _kb_edit(self):
        if self._is_text_input_focused():
            return
        if self.editing_idx is not None:
            self._commit_edit()
            return
        if self.selected_idx is None:
            return
        self._enter_edit_mode(self.selected_idx)

    def _kb_escape(self):
        if self.editing_idx is not None:
            self._cancel_edit()

    def _on_arrow_up(self, event):
        if self._is_text_input_focused() or self.editing_idx is not None:
            return
        if self.selected_idx is None or self.selected_idx == 0:
            return
        self.selected_idx -= 1
        self._update_highlight(scroll_into_view=True)

    def _on_arrow_down(self, event):
        if self._is_text_input_focused() or self.editing_idx is not None:
            return
        if self.selected_idx is None:
            return
        if self.selected_idx + 1 < len(self.paragraphs):
            self.selected_idx += 1
            self._update_highlight(scroll_into_view=True)

    def _jump_next_attention(self):
        """Select the next paragraph that still needs attention: one with
        no speaker assigned, or (when confidence shading is on) one
        containing a low-confidence word. Wraps past the end."""
        n = len(self.paragraphs)
        if n == 0:
            return
        buckets = (self._bucket_words_by_paragraph()
                   if (self.show_confidence and self.word_conf) else None)

        def needs_attention(i):
            if self.speakers[i] is None:
                return True
            if buckets is not None:
                return any(p is not None and p < 0.6 for _, p in buckets[i])
            return False

        start = (self.selected_idx + 1) if self.selected_idx is not None else 0
        for off in range(n):
            i = (start + off) % n
            if needs_attention(i):
                self.selected_idx = i
                self._update_highlight(scroll_into_view=True)
                return

    # ----- Edit mode -------------------------------------------------------

    def _enter_edit_mode(self, idx):
        if idx is None or not (0 <= idx < len(self.paragraphs)):
            return
        try:
            txt_start = self.text.index(f"txt_{idx}_start")
            line_end = self.text.index(f"{txt_start} lineend")
        except tk.TclError:
            return
        self._edit_original = self.text.get(txt_start, line_end)
        self.editing_idx = idx
        self.text.config(state="normal")
        self._update_highlight(scroll_into_view=True)
        # Place cursor at end of body, no selection.
        self.text.mark_set("insert", line_end)
        self.text.focus_set()
        try:
            self.text.tag_remove("sel", "1.0", "end")
        except tk.TclError:
            pass

    def _commit_edit(self):
        idx = self.editing_idx
        if idx is None:
            return
        try:
            txt_start = self.text.index(f"txt_{idx}_start")
            line_end = self.text.index(f"{txt_start} lineend")
            new_text = self.text.get(txt_start, line_end).strip()
        except tk.TclError:
            self._cancel_edit()
            return
        if not new_text:
            # Treat empty as cancel - preserve the original paragraph.
            self._cancel_edit()
            return
        # Only record an undo step if the text actually changed.
        if new_text != (self._edit_original or "").strip():
            self._push_undo()
        para = self.paragraphs[idx]
        if para:
            start_t = para[0][0]
            end_t = para[-1][1]
            self.paragraphs[idx] = [(start_t, end_t, new_text)]
        self.editing_idx = None
        self._edit_original = None
        # Anchor on the just-edited paragraph so the page doesn't jump.
        self._render_all(anchor_idx=idx)
        self._update_highlight(scroll_into_view=False)

    def _cancel_edit(self):
        idx = self.editing_idx
        self.editing_idx = None
        self._edit_original = None
        self._render_all(anchor_idx=idx)
        self._update_highlight(scroll_into_view=False)

    # ----- Split / merge helpers ------------------------------------------

    def _do_split(self, idx, offset, body_full):
        """Split paragraphs[idx] at character `offset` within the body."""
        para = self.paragraphs[idx]
        if not para:
            return
        # Build joined body (matches what was rendered).
        joined = " ".join(seg[2] for seg in para)
        leading_strip = len(joined) - len(joined.lstrip())
        adjusted_offset = offset + leading_strip
        if adjusted_offset <= 0 or adjusted_offset >= len(joined):
            return

        seg_starts = []
        running = ""
        for k, (_, _, t) in enumerate(para):
            if k > 0:
                running += " "
            seg_starts.append(len(running))
            running += t

        split_seg = None
        split_within = 0
        for k in range(len(para)):
            seg_start = seg_starts[k]
            seg_end = seg_start + len(para[k][2])
            if adjusted_offset <= seg_start:
                split_seg = k
                split_within = 0
                break
            if adjusted_offset <= seg_end:
                split_seg = k
                split_within = adjusted_offset - seg_start
                break
        if split_seg is None:
            return

        if split_within == 0:
            first = list(para[:split_seg])
            second = list(para[split_seg:])
        else:
            seg_start_t, seg_end_t, seg_text = para[split_seg]
            text_before = seg_text[:split_within].rstrip()
            text_after = seg_text[split_within:].lstrip()
            # Estimate when the split point occurs so the two halves get
            # real start/end times. Without this both halves would keep the
            # whole segment's span, and playing the second half would start
            # at the FIRST half's audio. Prefer the word timestamps when
            # captured; otherwise interpolate by character position.
            split_t = self._time_at_body_offset(para, joined, adjusted_offset)
            if split_t is None and seg_text:
                split_t = (seg_start_t
                           + (seg_end_t - seg_start_t)
                           * (split_within / len(seg_text)))
            if split_t is None:
                split_t = seg_start_t
            split_t = max(seg_start_t, min(float(split_t), seg_end_t))
            first = list(para[:split_seg])
            if text_before:
                first.append((seg_start_t, split_t, text_before))
            second = []
            if text_after:
                second.append((split_t, seg_end_t, text_after))
            second.extend(para[split_seg + 1:])

        if not first or not second:
            return

        self._push_undo()
        existing_speaker = self.speakers[idx]
        self.paragraphs[idx] = first
        self.paragraphs.insert(idx + 1, second)
        self.speakers.insert(idx + 1, existing_speaker)
        self.selected_idx = idx + 1
        # Anchor on the original (now first half) paragraph so the page
        # doesn't jump - the new second-half paragraph appears just below.
        self._render_all(anchor_idx=idx)
        self._update_highlight(scroll_into_view=False)

    # ----- Save / cancel ---------------------------------------------------

    def _on_name_changed(self, letter):
        self.speaker_names[letter] = self.name_vars[letter].get()
        self._render_all()
        self._schedule_autosave()

    def _resolved_speakers(self):
        return [self.speaker_names.get(letter) if letter else None
                for letter in self.speakers]

    def _on_save_clicked(self):
        if self.editing_idx is not None:
            self._commit_edit()
        for letter in self.SPEAKER_LETTERS:
            self.speaker_names[letter] = self.speaker_names[letter].strip()
        self.on_save_cb(self.paragraphs, self._resolved_speakers())

    def _on_save_revision_clicked(self):
        if self.editing_idx is not None:
            self._commit_edit()
        for letter in self.SPEAKER_LETTERS:
            self.speaker_names[letter] = self.speaker_names[letter].strip()
        self.on_save_revision_cb(self.paragraphs, self._resolved_speakers())

    def _on_cancel_clicked(self):
        if self.editing_idx is not None:
            self._commit_edit()
        if self.loaded:
            if not messagebox.askyesno(
                "Close without saving?",
                "Close the review pane without saving any changes?\n\n"
                "The original file on disk will not be modified.",
                default="no",
            ):
                return
        self.on_cancel_cb()

    def destroy(self):
        # Stop any playing audio and cancel pending after() callbacks; the
        # widget bindings themselves are reaped with the widget.
        self._stop_playback()
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except (tk.TclError, ValueError):
                pass
            self._autosave_after_id = None
        super().destroy()

    # ----- Compatibility with row-pane callers ----------------------------

    # WhisperGUI's preset-speakers code calls _refresh_all_rows after
    # mutating .speakers / .speaker_names. For this pane the equivalent
    # is a re-render.
    def _refresh_all_rows(self):
        self._render_all()


class WhisperGUI:

    def __init__(self, root):
        self.root = root
        self.root.title(f"Transcribr {__version__}")
        # The tabbed layout is far more compact than the old stacked one, so
        # the window can be shorter; the progress log takes the slack.
        self.root.geometry("900x780")
        self.root.minsize(700, 620)

        self.queue: "queue.Queue" = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.last_output = None
        # When a batch (multi-file) run is in progress this holds its state
        # dict; None otherwise. See _start_batch().
        self._batch = None

        self._build_ui()
        self._poll_queue()
        # After the window is up, offer to restore a crashed review session
        # if one was autosaved. Deferred so the main window paints first.
        self.root.after(300, self._maybe_offer_autosave_restore)

    # ----- UI construction ---------------------------------------------------

    def _build_ui(self):
        # Menu bar with File → Open Transcript...
        # On macOS the menu attaches to the system menubar at the top of
        # the screen; on Windows/Linux it attaches to the window itself.
        menubar = tk.Menu(self.root)

        # macOS application menu. Using the special "apple" name makes Tk
        # put these items at the top of the app menu (in place of "About
        # Python"); the menu's *title* comes from the process bundle name,
        # which main() rewrites to "Transcribr" when pyobjc is available.
        if sys.platform == "darwin":
            app_menu = tk.Menu(menubar, tearoff=False, name="apple")
            app_menu.add_command(label="About Transcribr",
                                 command=self._show_about)
            app_menu.add_separator()
            menubar.add_cascade(menu=app_menu)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(
            label="Open Transcript...",
            command=self._load_transcript,
        )
        self.recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="Open Recent", menu=self.recent_menu)
        self._refresh_recent_menu()
        menubar.add_cascade(label="File", menu=file_menu)

        # View -> Appearance. "Follow System" tracks the OS light/dark
        # setting (via darkdetect); the explicit options override it.
        view_menu = tk.Menu(menubar, tearoff=False)
        appearance_menu = tk.Menu(view_menu, tearoff=False)
        self.theme_var = tk.StringVar(value="auto")
        for label, value in (("Follow System", "auto"),
                             ("Light", "light"),
                             ("Dark", "dark")):
            appearance_menu.add_radiobutton(
                label=label, value=value, variable=self.theme_var,
                command=self._on_theme_changed)
        view_menu.add_cascade(label="Appearance", menu=appearance_menu)
        menubar.add_cascade(label="View", menu=view_menu)

        # Standard macOS Window menu (Tk manages its contents: minimise,
        # zoom, the window list).
        if sys.platform == "darwin":
            window_menu = tk.Menu(menubar, tearoff=False, name="window")
            menubar.add_cascade(label="Window", menu=window_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About Transcribr",
                              command=self._show_about)
        readme_path = _find_readme()
        help_menu.add_command(
            label="View README",
            command=lambda: _open_path(readme_path),
            state="normal" if readme_path else "disabled",
        )
        help_menu.add_separator()
        help_menu.add_command(
            label="Open Log File",
            command=lambda: _open_path(_log_file_path()),
        )
        help_menu.add_command(
            label=f"{_REVEAL_LABEL} (log file)",
            command=lambda: _reveal_path(_log_file_path()),
        )
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        # We keep a reference to the main frame so we can hide it (without
        # destroying it) when entering the review pane after a transcription.
        self.main_frame = ttk.Frame(self.root, padding=12)
        self.main_frame.pack(fill="both", expand=True)
        main = self.main_frame

        # Settings are grouped into tabs to keep the window compact. The
        # notebook holds the input/options; the Run controls, status line,
        # progress log and bottom buttons live below it so they stay visible
        # whichever tab is selected.
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="x", pady=(0, 8))

        file_tab = ttk.Frame(self.notebook, padding=8)
        model_tab = ttk.Frame(self.notebook, padding=8)
        advanced_tab = ttk.Frame(self.notebook, padding=8)
        recent_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(file_tab, text="File")
        self.notebook.add(model_tab, text="Model")
        self.notebook.add(advanced_tab, text="Advanced")
        self.notebook.add(recent_tab, text="Recent")

        # File tab: everything about *this* job's files - input/output,
        # the file description (it informs both Whisper and the document
        # title), and the batch queue.
        self._build_file_section(file_tab)
        self._build_prompt_section(file_tab)
        self._build_queue_section(file_tab)

        # Model tab: which engine/model does the work and how it decodes.
        self._build_model_section(model_tab)
        self._build_advanced_section(model_tab)

        # Advanced tab: post-processing - paragraph grouping / review
        # options and extra technical output formats.
        self._build_paragraph_section(advanced_tab)
        self._build_extra_outputs_section(advanced_tab)

        # Recent tab: reopen recently transcribed/edited transcripts.
        self._build_recent_section(recent_tab)

        # Always-visible controls below the notebook.
        self._build_run_section(main)
        self._build_log_section(main)
        self._build_bottom_section(main)

        # Will hold the ReviewPane while it's visible.
        self.review_pane = None
        # Stashed paragraphs_ready info so the save callback knows what to write.
        self._pending_review_info = None

        # Restore saved settings, then start persisting on close.
        self._apply_settings(_settings_load())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Apply the (possibly just-restored) theme to everything built
        # above - ttk widgets via sv-ttk plus our plain-tk widgets.
        self._retheme()

    def _build_file_section(self, parent):
        f = ttk.LabelFrame(parent, text="File", padding=8)
        f.pack(fill="x", pady=(0, 8))

        # Drop zone: a large click/drop target drawn on a Canvas (dashed
        # border + prompt text). Redraws on resize and theme switches.
        self._drop_hover = False
        self.drop_canvas = tk.Canvas(
            f, height=64, highlightthickness=0, cursor="hand2")
        self.drop_canvas.grid(row=0, column=0, columnspan=3,
                              sticky="ew", pady=(0, 8))
        self.drop_canvas.bind("<Button-1>", lambda _e: self._pick_input())
        self.drop_canvas.bind("<Configure>",
                              lambda _e: self._redraw_drop_zone())

        ttk.Label(f, text="Input:").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.input_var = tk.StringVar()
        self.input_var.trace_add("write", self._on_input_changed)
        self.input_entry = ttk.Entry(f, textvariable=self.input_var)
        self.input_entry.grid(row=1, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(f, text="Browse...", command=self._pick_input).grid(
            row=1, column=2)

        ttk.Label(f, text="Output:").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.output_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.output_var).grid(
            row=2, column=1, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(f, text="Browse...", command=self._pick_output).grid(
            row=2, column=2, pady=(6, 0))

        ttk.Label(f, text="Format:").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.output_format_var = tk.StringVar(value="docx")
        fmt_frame = ttk.Frame(f)
        fmt_frame.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Radiobutton(fmt_frame, text=".txt (plain text)",
                        variable=self.output_format_var, value="txt",
                        command=self._on_format_changed).pack(side="left")
        ttk.Radiobutton(fmt_frame, text=".docx (Word)",
                        variable=self.output_format_var, value="docx",
                        command=self._on_format_changed).pack(side="left", padx=(16, 0))
        ttk.Radiobutton(fmt_frame, text=".pdf (PDF)",
                        variable=self.output_format_var, value="pdf",
                        command=self._on_format_changed).pack(side="left", padx=(16, 0))

        f.columnconfigure(1, weight=1)

        # Register drop targets if tkinterdnd2 is available. Whole window
        # plus the drop zone and input Entry, so dropping anywhere works.
        if DND_AVAILABLE:
            for widget in (self.root, self.input_entry, f,
                           self.drop_canvas):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except (AttributeError, tk.TclError):
                    # Not all widget types support dnd registration.
                    pass
            # Highlight the drop zone while a drag hovers over it.
            try:
                self.drop_canvas.dnd_bind("<<DropEnter>>",
                                          self._on_drop_enter)
                self.drop_canvas.dnd_bind("<<DropLeave>>",
                                          self._on_drop_leave)
            except (AttributeError, tk.TclError):
                pass

    def _on_drop_enter(self, event):
        """tkdnd treats the <<DropEnter>> handler's return value as the
        accepted action - returning None refuses the drop outright, which
        is why this must echo event.action back."""
        self._set_drop_hover(True)
        return getattr(event, "action", "copy")

    def _on_drop_leave(self, event):
        self._set_drop_hover(False)
        return getattr(event, "action", None)

    def _set_drop_hover(self, hovering):
        self._drop_hover = bool(hovering)
        self._redraw_drop_zone()

    def _redraw_drop_zone(self):
        """(Re)draw the dashed drop target; called on resize, theme switch,
        and drag-hover changes."""
        c = self.drop_canvas
        pal = _palette()
        c.delete("all")
        width = c.winfo_width()
        if width <= 1:  # not laid out yet; <Configure> will call us again
            width = c.winfo_reqwidth()
        height = int(c.cget("height"))
        c.config(background=pal["drop_hover"] if self._drop_hover
                 else pal["drop_bg"])
        c.create_rectangle(
            3, 3, width - 3, height - 3,
            dash=(6, 4), width=2, outline=pal["drop_border"])
        prompt = "Drop audio or video here   —   or click to browse"
        if not DND_AVAILABLE:
            prompt = "Click to choose an audio or video file"
        c.create_text(width / 2, height / 2, text=prompt,
                      fill=pal["drop_fg"], font=("TkDefaultFont", 12))

    def _on_drop(self, event):
        """Handle a file drop. event.data is a TkDnD-encoded path list.

        Dropping a single file fills the Input field (single-file flow).
        Dropping several files adds them all to the batch queue."""
        # A finished drop doesn't always deliver <<DropLeave>>; clear the
        # hover highlight here too.
        self._set_drop_hover(False)
        # TkDnD wraps paths with spaces in {curly braces}; tk.splitlist handles
        # that uniformly across platforms.
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            paths = [event.data]
        cleaned = [str(p).strip().strip("{}").strip('"') for p in paths]
        cleaned = [p for p in cleaned if p]
        if len(cleaned) == 1:
            self.input_var.set(cleaned[0])
        elif len(cleaned) > 1:
            self._batch_add_paths(cleaned)
        return event.action if hasattr(event, "action") else None

    def _build_queue_section(self, parent):
        """A batch queue: drop or add several media files, then Run
        transcribes them one after another, writing each transcript next to
        its source file (no interactive review - open them afterwards from
        File -> Open Recent to label speakers)."""
        f = ttk.LabelFrame(parent, text="Batch queue (optional)", padding=8)
        f.pack(fill="x", pady=(0, 8))

        ttk.Label(
            f,
            text=(
                "Add several files to transcribe them in one unattended run. "
                "Each transcript is saved next to its source file. Leave this "
                "empty to use the single Input file above."
            ),
            foreground="gray", wraplength=820, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        list_wrap = ttk.Frame(f)
        list_wrap.grid(row=1, column=0, sticky="ew")
        self.batch_listbox = tk.Listbox(list_wrap, height=4,
                                        activestyle="none",
                                        selectmode="extended")
        self.batch_listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_wrap, orient="vertical",
                           command=self.batch_listbox.yview)
        sb.pack(side="right", fill="y")
        self.batch_listbox.config(yscrollcommand=sb.set)

        btns = ttk.Frame(f)
        btns.grid(row=1, column=1, sticky="n", padx=(8, 0))
        ttk.Button(btns, text="Add files...",
                   command=self._batch_add_files).pack(fill="x")
        ttk.Button(btns, text="Remove selected",
                   command=self._batch_remove_selected).pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="Clear",
                   command=self._batch_clear).pack(fill="x", pady=(4, 0))

        f.columnconfigure(0, weight=1)

    # ----- Batch queue management --------------------------------------------

    def _batch_files(self):
        """Current queued input paths, in order."""
        return list(self.batch_listbox.get(0, "end"))

    def _batch_add_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose audio or video files to queue",
            filetypes=[
                ("Audio/Video",
                 "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus "
                 "*.mp4 *.mov *.mkv *.avi *.webm"),
                ("All files", "*.*"),
            ],
        )
        if paths:
            self._batch_add_paths(paths)

    def _batch_add_paths(self, paths):
        """Append paths to the queue, skipping ones already present."""
        existing = set(self._batch_files())
        for p in paths:
            p = str(p)
            if p and p not in existing:
                self.batch_listbox.insert("end", p)
                existing.add(p)

    def _batch_remove_selected(self):
        # Delete from the bottom up so indices stay valid.
        for idx in sorted(self.batch_listbox.curselection(), reverse=True):
            self.batch_listbox.delete(idx)

    def _batch_clear(self):
        self.batch_listbox.delete(0, "end")

    # ----- Recent tab ---------------------------------------------------------

    def _build_recent_section(self, parent):
        """A browsable list of recently transcribed/opened transcripts.
        Same data as File -> Open Recent, in a roomier form: double-click
        (or 'Open for review') re-opens a transcript for labelling."""
        f = ttk.LabelFrame(parent, text="Recent transcripts", padding=8)
        f.pack(fill="both", expand=True)

        list_wrap = ttk.Frame(f)
        list_wrap.pack(side="left", fill="both", expand=True)
        self.recent_listbox = tk.Listbox(list_wrap, height=9,
                                         activestyle="none")
        self.recent_listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_wrap, orient="vertical",
                           command=self.recent_listbox.yview)
        sb.pack(side="right", fill="y")
        self.recent_listbox.config(yscrollcommand=sb.set)
        self.recent_listbox.bind(
            "<Double-Button-1>", lambda _e: self._recent_open_selected())

        btns = ttk.Frame(f)
        btns.pack(side="right", fill="y", padx=(8, 0))
        ttk.Button(btns, text="Open for review",
                   command=self._recent_open_selected).pack(fill="x")
        ttk.Button(btns, text=_REVEAL_LABEL,
                   command=self._recent_reveal_selected).pack(
            fill="x", pady=(4, 0))
        ttk.Button(btns, text="Clear list",
                   command=self._clear_recent).pack(fill="x", pady=(4, 0))

        # Parallel list of full paths backing the listbox rows.
        self._recent_paths = []
        self._refresh_recent_tab()

    def _refresh_recent_tab(self):
        """Rebuild the Recent tab's list from disk. No-op before the tab
        exists (the recent-menu refresh runs once during early build)."""
        if not hasattr(self, "recent_listbox"):
            return
        self._recent_paths = [
            p for p in _recent_load() if Path(p).exists()
        ]
        self.recent_listbox.delete(0, "end")
        for p in self._recent_paths:
            path = Path(p)
            self.recent_listbox.insert(
                "end", f" {path.name}    —    {path.parent}")

    def _recent_selected_path(self):
        sel = self.recent_listbox.curselection()
        if not sel or sel[0] >= len(self._recent_paths):
            return None
        return self._recent_paths[sel[0]]

    def _recent_open_selected(self):
        path = self._recent_selected_path()
        if path:
            self._open_recent(path)

    def _recent_reveal_selected(self):
        path = self._recent_selected_path()
        if path and Path(path).exists():
            _reveal_path(path)

    def _build_model_section(self, parent):
        f = ttk.LabelFrame(parent, text="Model", padding=8)
        f.pack(fill="x", pady=(0, 8))

        # Engine selector. Only shows engines actually importable in this
        # Python (probed once at module load).
        ttk.Label(f, text="Engine:").grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        engine_choices = ([name for _, name in AVAILABLE_ENGINES]
                          or ["(no engine installed - install whisper, "
                              "faster-whisper, or mlx-whisper)"])
        self.engine_var = tk.StringVar(value=engine_choices[0])
        ttk.Combobox(
            f, textvariable=self.engine_var, values=engine_choices,
            state="readonly", width=36,
        ).grid(row=0, column=1, columnspan=5, sticky="w")

        ttk.Label(f, text="Model:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.model_var = tk.StringVar(value="large-v3-turbo")
        ttk.Combobox(f, textvariable=self.model_var, values=WHISPER_MODELS,
                     state="readonly", width=14).grid(
            row=1, column=1, sticky="w", padx=(0, 16), pady=(6, 0))

        ttk.Label(f, text="Language:").grid(
            row=1, column=2, sticky="w", padx=(0, 6), pady=(6, 0))
        self.language_var = tk.StringVar(value="English")
        ttk.Combobox(f, textvariable=self.language_var,
                     values=[name for name, _ in LANGUAGES],
                     state="readonly", width=20).grid(
            row=1, column=3, sticky="w", padx=(0, 16), pady=(6, 0))

        ttk.Label(f, text="Task:").grid(
            row=1, column=4, sticky="w", padx=(0, 6), pady=(6, 0))
        self.task_var = tk.StringVar(value="transcribe")
        ttk.Combobox(f, textvariable=self.task_var,
                     values=["transcribe", "translate"],
                     state="readonly", width=12).grid(
            row=1, column=5, sticky="w", pady=(6, 0))

    def _build_prompt_section(self, parent):
        f = ttk.LabelFrame(
            parent,
            text="Description of file (helps with proper nouns, jargon, names)",
            padding=8,
        )
        f.pack(fill="x", pady=(0, 8))
        self.prompt_text = tk.Text(f, height=1, wrap="word")
        self.prompt_text.pack(fill="x")

    def _build_paragraph_section(self, parent):
        f = ttk.LabelFrame(parent, text="Paragraph grouping", padding=8)
        f.pack(fill="x", pady=(0, 8))

        # First row: gap-detection setting + timestamp toggle.
        row1 = ttk.Frame(f)
        row1.pack(fill="x")
        ttk.Label(row1, text="Pause that triggers a new paragraph:").pack(side="left")
        self.gap_var = tk.DoubleVar(value=1.5)
        ttk.Spinbox(row1, from_=0.0, to=10.0, increment=0.1,
                    textvariable=self.gap_var, width=6).pack(side="left", padx=6)
        ttk.Label(row1, text="seconds").pack(side="left")

        self.show_timestamp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="Show timestamps in output",
                        variable=self.show_timestamp_var).pack(
            side="left", padx=(24, 0))

        # Second row: review toggle.
        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(6, 0))
        self.review_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row2,
            text="Review and label speakers before saving",
            variable=self.review_var,
        ).pack(side="left")

    def _build_extra_outputs_section(self, parent):
        f = ttk.LabelFrame(parent, text="Additional outputs",
                           padding=8)
        f.pack(fill="x", pady=(0, 8))

        self.extra_json_var = tk.BooleanVar(value=False)
        self.extra_srt_var  = tk.BooleanVar(value=False)
        self.extra_vtt_var  = tk.BooleanVar(value=False)
        self.extra_tsv_var  = tk.BooleanVar(value=False)

        ttk.Checkbutton(f, text="JSON (full Whisper result, including confidences)",
                        variable=self.extra_json_var).grid(
            row=0, column=0, sticky="w")
        ttk.Checkbutton(f, text="SRT (subtitle file)",
                        variable=self.extra_srt_var).grid(
            row=0, column=1, sticky="w", padx=(20, 0))
        ttk.Checkbutton(f, text="VTT (subtitle file)",
                        variable=self.extra_vtt_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(f, text="TSV (tab-separated start/end/text)",
                        variable=self.extra_tsv_var).grid(
            row=1, column=1, sticky="w", padx=(20, 0), pady=(4, 0))

    def _build_advanced_section(self, parent):
        f = ttk.LabelFrame(parent, text="Decoding options", padding=8)
        f.pack(fill="x", pady=(0, 8))

        ttk.Label(f, text="Temperature:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.temperature_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.1,
                    textvariable=self.temperature_var, width=6).grid(
            row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(f, text="Beam size:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.beam_size_var = tk.IntVar(value=5)
        ttk.Spinbox(f, from_=1, to=20, textvariable=self.beam_size_var,
                    width=6).grid(row=0, column=3, sticky="w", padx=(0, 16))

        ttk.Label(f, text="Best of:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.best_of_var = tk.IntVar(value=5)
        ttk.Spinbox(f, from_=1, to=20, textvariable=self.best_of_var,
                    width=6).grid(row=0, column=5, sticky="w")

        ttk.Label(f, text="Compression ratio threshold:").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=(0, 6), pady=(6, 0))
        self.compression_var = tk.DoubleVar(value=2.4)
        ttk.Spinbox(f, from_=0.0, to=10.0, increment=0.1,
                    textvariable=self.compression_var, width=6).grid(
            row=1, column=2, sticky="w", padx=(0, 16), pady=(6, 0))

        ttk.Label(f, text="Logprob threshold:").grid(
            row=1, column=3, sticky="w", padx=(0, 6), pady=(6, 0))
        self.logprob_var = tk.DoubleVar(value=-1.0)
        ttk.Spinbox(f, from_=-10.0, to=0.0, increment=0.1,
                    textvariable=self.logprob_var, width=6).grid(
            row=1, column=4, sticky="w", pady=(6, 0))

        ttk.Label(f, text="No-speech threshold:").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(0, 6), pady=(6, 0))
        self.no_speech_var = tk.DoubleVar(value=0.6)
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05,
                    textvariable=self.no_speech_var, width=6).grid(
            row=2, column=2, sticky="w", padx=(0, 16), pady=(6, 0))

        self.condition_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Condition on previous text",
                        variable=self.condition_var).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.word_ts_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Word-level timestamps",
                        variable=self.word_ts_var).grid(
            row=3, column=3, columnspan=3, sticky="w", pady=(6, 0))

        # Highlighting low-confidence words in the review pane needs the
        # per-word probabilities, which only come with word timestamps - so
        # ticking this implicitly enables them for the run.
        self.confidence_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f,
            text="Highlight low-confidence words in review "
                 "(enables word timestamps; a little slower)",
            variable=self.confidence_var,
        ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _build_run_section(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(0, 8))
        # Accent style marks this as the primary action (sv-ttk); plain
        # ttk falls back to the standard button look.
        self.run_btn = ttk.Button(f, text="Run Transcription",
                                  style="Accent.TButton",
                                  command=self._on_run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(f, text="Stop", command=self._on_stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        # Load an existing transcript for re-editing. This is the
        # button-equivalent of File -> Open Transcript... in the menu.
        ttk.Button(
            f,
            text="Load existing transcript",
            command=self._load_transcript,
        ).pack(side="left", padx=(16, 0))

    def _build_log_section(self, parent):
        # Progress card: current file + percentage, a progress bar, and a
        # one-line status (elapsed / remaining / speed). The raw engine
        # log lives below it, collapsed behind a "Show details" toggle.
        card = ttk.LabelFrame(parent, text="Progress", padding=10)
        card.pack(fill="x", pady=(0, 8))

        top = ttk.Frame(card)
        top.pack(fill="x")
        self.progress_file_var = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.progress_file_var,
                  font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.progress_pct_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.progress_pct_var,
                  foreground="gray").pack(side="right")

        self.progress_bar = ttk.Progressbar(
            card, mode="determinate", maximum=100.0)
        self.progress_bar.pack(fill="x", pady=(8, 6))

        bottom = ttk.Frame(card)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        self.status_label = ttk.Label(
            bottom, textvariable=self.status_var, foreground="gray")
        self.status_label.pack(side="left")
        self.details_btn = ttk.Button(
            bottom, text="Show details ▸", width=14,
            command=self._toggle_details)
        self.details_btn.pack(side="right")

        # Details: the raw engine output, hidden by default. Packed (and
        # forgotten) on demand by _set_details, just above the bottom
        # button row, so showing/hiding visibly reflows the window.
        self.details_visible = False
        self.log_frame = ttk.LabelFrame(parent, text="Details", padding=8)
        self.output_text = scrolledtext.ScrolledText(
            self.log_frame, height=9, wrap="word", state="disabled")
        self.output_text.pack(fill="both", expand=True)

    def _toggle_details(self):
        self._set_details(not self.details_visible)
        self._save_settings()

    def _set_details(self, visible):
        """Show/hide the raw-log frame. Unconditional (no early-return on
        matching state) so a click always forces widget reality - packing,
        button label - to agree with the requested state."""
        self.details_visible = bool(visible)
        if self.details_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(0, 8),
                                before=self.bottom_frame)
            self.details_btn.config(text="Hide details ▾")
        else:
            self.log_frame.pack_forget()
            self.details_btn.config(text="Show details ▸")

    # ----- Settings persistence ----------------------------------------------

    def _collect_settings(self):
        """Snapshot the current UI settings into a JSON-serialisable dict."""
        try:
            s = {
                "engine": self.engine_var.get(),
                "model": self.model_var.get(),
                "language": self.language_var.get(),
                "task": self.task_var.get(),
                "output_format": self.output_format_var.get(),
                "prompt": self.prompt_text.get("1.0", "end-1c"),
                "gap": self.gap_var.get(),
                "show_timestamp": self.show_timestamp_var.get(),
                "review": self.review_var.get(),
                "temperature": self.temperature_var.get(),
                "beam_size": self.beam_size_var.get(),
                "best_of": self.best_of_var.get(),
                "compression_ratio_threshold": self.compression_var.get(),
                "logprob_threshold": self.logprob_var.get(),
                "no_speech_threshold": self.no_speech_var.get(),
                "condition_on_previous_text": self.condition_var.get(),
                "word_timestamps": self.word_ts_var.get(),
                "extra_json": self.extra_json_var.get(),
                "extra_srt": self.extra_srt_var.get(),
                "extra_vtt": self.extra_vtt_var.get(),
                "extra_tsv": self.extra_tsv_var.get(),
            }
            if hasattr(self, "confidence_var"):
                s["highlight_confidence"] = self.confidence_var.get()
            s["theme"] = self.theme_var.get()
            s["show_details"] = self.details_visible
            return s
        except tk.TclError:
            return {}

    def _apply_settings(self, s):
        """Apply a saved settings dict to the UI vars, defensively: unknown
        or out-of-range values are ignored so a stale/corrupt file can't
        wedge the app."""
        if not s:
            return

        def set_choice(var, key, allowed):
            v = s.get(key)
            if isinstance(v, str) and v in allowed:
                var.set(v)

        def set_num(var, key):
            v = s.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                try:
                    var.set(v)
                except tk.TclError:
                    pass

        def set_bool(var, key):
            v = s.get(key)
            if isinstance(v, bool):
                var.set(v)

        set_choice(self.engine_var, "engine",
                   [name for _, name in AVAILABLE_ENGINES])
        set_choice(self.model_var, "model", list(WHISPER_MODELS))
        set_choice(self.language_var, "language",
                   [n for n, _ in LANGUAGES])
        set_choice(self.task_var, "task", ["transcribe", "translate"])
        set_choice(self.output_format_var, "output_format",
                   ["txt", "docx", "pdf"])

        prompt = s.get("prompt")
        if isinstance(prompt, str) and prompt:
            try:
                self.prompt_text.delete("1.0", "end")
                self.prompt_text.insert("1.0", prompt)
            except tk.TclError:
                pass

        set_num(self.gap_var, "gap")
        set_bool(self.show_timestamp_var, "show_timestamp")
        set_bool(self.review_var, "review")
        set_num(self.temperature_var, "temperature")
        set_num(self.beam_size_var, "beam_size")
        set_num(self.best_of_var, "best_of")
        set_num(self.compression_var, "compression_ratio_threshold")
        set_num(self.logprob_var, "logprob_threshold")
        set_num(self.no_speech_var, "no_speech_threshold")
        set_bool(self.condition_var, "condition_on_previous_text")
        set_bool(self.word_ts_var, "word_timestamps")
        set_bool(self.extra_json_var, "extra_json")
        set_bool(self.extra_srt_var, "extra_srt")
        set_bool(self.extra_vtt_var, "extra_vtt")
        set_bool(self.extra_tsv_var, "extra_tsv")
        if hasattr(self, "confidence_var"):
            set_bool(self.confidence_var, "highlight_confidence")
        set_choice(self.theme_var, "theme", ["auto", "light", "dark"])
        show_details = s.get("show_details")
        if isinstance(show_details, bool):
            self._set_details(show_details)

    def _save_settings(self):
        _settings_save(self._collect_settings())

    def _on_close(self):
        self._save_settings()
        self.root.destroy()

    # ----- Theming -----------------------------------------------------------

    def _on_theme_changed(self):
        self._retheme()
        self._save_settings()

    def _retheme(self):
        """Activate the selected theme and re-colour the plain-tk widgets
        that ttk theming doesn't reach."""
        _apply_theme(self.theme_var.get())
        # sv-ttk reacts to <<ThemeChanged>> with a tk_setPalette pass that
        # repaints plain-tk widgets; under Tk 9 that's delivered at idle
        # time, i.e. *after* the explicit colours below, clobbering them.
        # Flush the idle queue first so our palette wins.
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        pal = _palette()
        for widget in (self.prompt_text, self.output_text):
            try:
                widget.config(background=pal["text_bg"],
                              foreground=pal["text_fg"],
                              insertbackground=pal["insert"])
            except tk.TclError:
                pass
        for listbox in (self.batch_listbox, self.recent_listbox):
            try:
                listbox.config(
                    background=pal["text_bg"], foreground=pal["text_fg"],
                    selectbackground=pal["selected_bg"],
                    selectforeground=pal["text_fg"],
                )
            except tk.TclError:
                pass
        self._redraw_drop_zone()
        if (self.review_pane is not None
                and hasattr(self.review_pane, "apply_palette")):
            self.review_pane.apply_palette()

    def _build_bottom_section(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill="x")
        # _set_details packs the log frame just above this row.
        self.bottom_frame = f
        self.open_btn = ttk.Button(f, text="Open Output",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="left")
        self.reveal_btn = ttk.Button(f, text=_REVEAL_LABEL,
                                     command=self._reveal_output, state="disabled")
        self.reveal_btn.pack(side="left", padx=(6, 0))
        ttk.Button(f, text="About", command=self._show_about).pack(side="left", padx=(6, 0))
        ttk.Button(f, text="Clear Log", command=self._clear_log).pack(side="right")

    def _show_about(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("About Transcribr")
        dlg.transient(self.root)
        dlg.resizable(False, False)

        frame = ttk.Frame(dlg, padding=16)
        frame.pack()

        # Try to load the icon (PNG, since Tk's PhotoImage doesn't read
        # .ico or .icns directly). The image is stashed on the dialog so
        # it isn't garbage-collected while the dialog is open.
        png = Path(__file__).resolve().parent / "icon.png"
        if png.exists():
            try:
                img = tk.PhotoImage(file=str(png))
                icon_lbl = ttk.Label(frame, image=img)
                icon_lbl.image = img  # keep a reference
                icon_lbl.grid(row=0, column=0, sticky="n", padx=(0, 16))
            except tk.TclError:
                pass  # missing PNG support; fall through to text-only

        text_lbl = ttk.Label(
            frame, text=ABOUT_TEXT, wraplength=420, justify="left",
        )
        text_lbl.grid(row=0, column=1, sticky="nw")

        button_row = ttk.Frame(frame)
        button_row.grid(row=1, column=1, sticky="e", pady=(12, 0))

        # README button - looks for README.md / README.txt next to the
        # script first, then in the parent of the script's folder. Disabled
        # if not found.
        readme_path = _find_readme()
        readme_btn = ttk.Button(
            button_row, text="View README",
            command=lambda: _open_path(readme_path) if readme_path else None,
        )
        if not readme_path:
            readme_btn.config(state="disabled")
        readme_btn.pack(side="left", padx=(0, 6))

        ok_btn = ttk.Button(button_row, text="OK", command=dlg.destroy)
        ok_btn.pack(side="left")

        # Close on Enter / Escape, focus the OK button.
        dlg.bind("<Return>", lambda _e: dlg.destroy())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        ok_btn.focus_set()

        # Centre on the parent window.
        dlg.update_idletasks()
        px = self.root.winfo_rootx()
        py = self.root.winfo_rooty()
        pw = self.root.winfo_width()
        ph = self.root.winfo_height()
        dw = dlg.winfo_width()
        dh = dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")

        dlg.grab_set()
        dlg.wait_window()

    # ----- File pickers ------------------------------------------------------

    def _pick_input(self):
        path = filedialog.askopenfilename(
            title="Choose audio or video file",
            filetypes=[
                ("Audio/Video",
                 "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus "
                 "*.mp4 *.mov *.mkv *.avi *.webm"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.input_var.set(path)
            # _on_input_changed (via trace) updates the output path.

    def _on_input_changed(self, *_):
        """Whenever the input path changes, derive the output path from it."""
        in_path = self.input_var.get().strip()
        if in_path:
            ext = self.output_format_var.get() if hasattr(self, "output_format_var") else "txt"
            self.output_var.set(
                str(Path(in_path).with_suffix(f".transcript.{ext}")))

    def _on_format_changed(self):
        """When the user picks a different output format, swap the extension
        on the existing output path so it stays in sync."""
        out = self.output_var.get().strip()
        new_ext = "." + self.output_format_var.get()
        if out:
            p = Path(out)
            # Only swap if the existing extension is a known one - don't
            # clobber an unusual user-supplied extension.
            if p.suffix.lower() in (".txt", ".docx", ".pdf"):
                self.output_var.set(str(p.with_suffix(new_ext)))

    def _pick_output(self):
        initial = self.output_var.get() or ""
        fmt = self.output_format_var.get()
        ext = "." + fmt
        if fmt == "docx":
            ftypes = [("Word document", "*.docx"), ("All files", "*.*")]
        elif fmt == "pdf":
            ftypes = [("PDF document", "*.pdf"), ("All files", "*.*")]
        else:
            ftypes = [("Text", "*.txt"), ("All files", "*.*")]
        path = filedialog.asksaveasfilename(
            title="Save paragraphified transcript as",
            defaultextension=ext,
            initialfile=Path(initial).name if initial
                        else f"output.transcript{ext}",
            initialdir=str(Path(initial).parent) if initial else "",
            filetypes=ftypes,
        )
        if path:
            self.output_var.set(path)

    # ----- Run / cancel ------------------------------------------------------

    def _build_params(self, in_path, out_path, *, review_before_save):
        """Assemble the worker params dict from the current UI settings for
        a single (in_path -> out_path) job. Returns None (after showing an
        error) if no transcription engine is installed."""
        if not AVAILABLE_ENGINES:
            messagebox.showerror(
                "No transcription engine installed",
                "No Whisper engine is installed in this Python.\n\n"
                "Install at least one:\n"
                "  pip install openai-whisper\n"
                "  pip install faster-whisper\n"
                "  pip install mlx-whisper   (Apple Silicon only)\n",
            )
            return None

        lang_name = self.language_var.get()
        lang_code = next((c for n, c in LANGUAGES if n == lang_name), "en")

        # Build the list of additional Whisper output formats requested.
        extra_formats = []
        if self.extra_json_var.get(): extra_formats.append("json")
        if self.extra_srt_var.get():  extra_formats.append("srt")
        if self.extra_vtt_var.get():  extra_formats.append("vtt")
        if self.extra_tsv_var.get():  extra_formats.append("tsv")

        # Read once: the same text is fed to Whisper as initial_prompt
        # (helps with proper noun accuracy) and used as the document title.
        description = self.prompt_text.get("1.0", "end").strip()

        # Probe the file's duration so the worker can compute an ETA. This
        # is best-effort: if ffprobe is missing or the file is unparseable
        # we just skip the ETA. Whisper itself doesn't need it.
        audio_duration = get_audio_duration(in_path)

        # Map the engine dropdown's display name back to its key.
        engine_display = self.engine_var.get()
        engine_key = next(
            (k for k, n in AVAILABLE_ENGINES if n == engine_display),
            "whisper",
        )

        return dict(
            input=in_path,
            output=out_path,
            engine=engine_key,
            model=self.model_var.get(),
            language=lang_code,
            task=self.task_var.get(),
            temperature=self.temperature_var.get(),
            beam_size=self.beam_size_var.get(),
            best_of=self.best_of_var.get(),
            compression_ratio_threshold=self.compression_var.get(),
            logprob_threshold=self.logprob_var.get(),
            no_speech_threshold=self.no_speech_var.get(),
            condition_on_previous_text=self.condition_var.get(),
            # Confidence highlighting requires word timestamps, so enable
            # them whenever either option is ticked.
            word_timestamps=self.word_ts_var.get() or self.confidence_var.get(),
            highlight_confidence=self.confidence_var.get(),
            initial_prompt=description or None,
            # The description doubles as the document title; with no
            # description, title the document after the source file. (The
            # filename is NOT fed to Whisper as a prompt - recorder names
            # like REC_0042 would only mislead it.)
            title=description or Path(in_path).name,
            gap=self.gap_var.get(),
            extra_formats=extra_formats,
            output_format=self.output_format_var.get(),
            show_timestamp=self.show_timestamp_var.get(),
            audio_duration=audio_duration,
            review_before_save=review_before_save,
        )

    def _on_run(self):
        # Persist the current settings at the start of every run so they
        # survive even if the app is later force-quit or crashes.
        self._save_settings()
        # If the batch queue has files, run them all unattended. Otherwise
        # fall back to the single Input-file flow (which can pause for
        # interactive review).
        if self._batch_files():
            self._start_batch()
            return

        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()
        if not in_path:
            messagebox.showerror("Missing input",
                                 "Please choose an input audio/video file, "
                                 "or add files to the batch queue.")
            return
        if not Path(in_path).exists():
            messagebox.showerror("File not found",
                                 f"Input file does not exist:\n{in_path}")
            return
        if not out_path:
            ext = self.output_format_var.get()
            out_path = str(Path(in_path).with_suffix(f".transcript.{ext}"))
            self.output_var.set(out_path)

        if Path(out_path).exists():
            if not messagebox.askyesno(
                "Overwrite existing file?",
                f"The output file already exists:\n\n{out_path}\n\n"
                "Do you want to overwrite it?",
                default="no",
            ):
                return

        params = self._build_params(
            in_path, out_path, review_before_save=self.review_var.get())
        if params is None:
            return

        self.run_btn.config(state="disabled", text="Running...")
        self.stop_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.reveal_btn.config(state="disabled")
        self._clear_log()
        self.progress_file_var.set(Path(in_path).name)
        self.status_var.set("Starting...")

        self.cancel_event.clear()
        self.worker = threading.Thread(
            target=transcribe_worker,
            args=(params, self.queue, self.cancel_event),
            daemon=True,
        )
        self.worker.start()

    # ----- Batch (multi-file) run --------------------------------------------

    def _start_batch(self):
        """Begin transcribing every file in the batch queue, one after
        another. Each file is written directly to its derived output path
        (no interactive review). Failures are recorded and the run carries
        on; a summary is shown at the end."""
        files = self._batch_files()
        if not files:
            return

        # Validate inputs and derive an output path per file.
        ext = self.output_format_var.get()
        items = []
        missing = []
        for in_path in files:
            if not Path(in_path).exists():
                missing.append(in_path)
                continue
            out_path = str(Path(in_path).with_suffix(f".transcript.{ext}"))
            items.append((in_path, out_path))
        if missing:
            messagebox.showerror(
                "Files not found",
                "These queued files no longer exist:\n\n"
                + "\n".join(missing),
            )
            return
        if not items:
            return

        # Warn once if any outputs already exist.
        existing = [o for _, o in items if Path(o).exists()]
        if existing:
            preview = "\n".join(existing[:8])
            if len(existing) > 8:
                preview += f"\n... and {len(existing) - 8} more"
            if not messagebox.askyesno(
                "Overwrite existing files?",
                f"{len(existing)} output file(s) already exist and will be "
                f"overwritten:\n\n{preview}\n\nContinue?",
                default="no",
            ):
                return

        # Confirm engine is available before committing to the run.
        probe = self._build_params(items[0][0], items[0][1],
                                   review_before_save=False)
        if probe is None:
            return

        self._batch = {
            "items": items,
            "index": 0,
            "succeeded": [],
            "failed": [],
            "stop": False,
        }
        self.run_btn.config(state="disabled", text="Running batch...")
        self.stop_btn.config(state="normal", text="Stop")
        self.open_btn.config(state="disabled")
        self.reveal_btn.config(state="disabled")
        self._clear_log()
        self._append_log(
            f"=== Batch: {len(items)} file(s) queued ===\n")
        self._start_batch_item()

    def _start_batch_item(self):
        b = self._batch
        if b is None:
            return
        idx = b["index"]
        in_path, out_path = b["items"][idx]
        n = len(b["items"])
        self._append_log(
            f"\n--- File {idx + 1} of {n}: {Path(in_path).name} ---\n")
        self.progress_file_var.set(
            f"{Path(in_path).name}  (file {idx + 1} of {n})")
        self.status_var.set("Starting...")
        self._set_progress(0)
        params = self._build_params(
            in_path, out_path, review_before_save=False)
        if params is None:
            # Engine vanished mid-run; abort the batch.
            self._finish_batch(stopped=True)
            return
        self.cancel_event.clear()
        self.worker = threading.Thread(
            target=transcribe_worker,
            args=(params, self.queue, self.cancel_event),
            daemon=True,
        )
        self.worker.start()

    def _batch_item_done(self, output_path, error):
        b = self._batch
        if b is None:
            return
        idx = b["index"]
        in_path = b["items"][idx][0]
        if error is not None:
            first_line = error.splitlines()[0] if error else "Unknown error"
            b["failed"].append((in_path, first_line))
            self._append_log(f"FAILED: {Path(in_path).name}: {first_line}\n")
        else:
            b["succeeded"].append(output_path)
            if output_path and Path(output_path).exists():
                _recent_add(output_path)
        # If the user asked to stop, end after the current file.
        if b["stop"]:
            self._finish_batch(stopped=True)
            return
        b["index"] += 1
        if b["index"] < len(b["items"]):
            self._start_batch_item()
        else:
            self._finish_batch(stopped=False)

    def _batch_cancelled(self, message):
        b = self._batch
        if b is None:
            return
        if message:
            self._append_log(f"\n=== Stopped: {message} ===\n")
        self._finish_batch(stopped=True)

    def _finish_batch(self, *, stopped):
        b = self._batch
        self._batch = None
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        succeeded = b["succeeded"] if b else []
        failed = b["failed"] if b else []
        if succeeded:
            self.last_output = succeeded[-1]
            self.open_btn.config(state="normal")
            self.reveal_btn.config(state="normal")
        self._refresh_recent_menu()

        head = "Batch stopped" if stopped else "Batch complete"
        self.status_var.set(head)
        self.progress_file_var.set(head)
        lines = [f"\n=== {head} ===",
                 f"Transcribed: {len(succeeded)}",
                 f"Failed: {len(failed)}"]
        for in_path, why in failed:
            lines.append(f"  - {Path(in_path).name}: {why}")
        lines.append("Open each transcript from File -> Open Recent to "
                     "review and label speakers.")
        self._append_log("\n".join(lines) + "\n")
        messagebox.showinfo(
            head,
            f"Transcribed {len(succeeded)} file(s).\n"
            f"Failed: {len(failed)}.\n\n"
            "Open each transcript from File -> Open Recent to review "
            "and label speakers.",
        )

    def _on_stop(self):
        # In a batch run, always honour Stop - mark the batch so it ends
        # after the current file rather than advancing to the next one,
        # even if we're momentarily between files.
        if self._batch is not None:
            self._batch["stop"] = True
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.stop_btn.config(state="disabled", text="Stopping...")
            self._append_log(
                "\n[Stop requested - finishing current segment "
                "and saving partial transcript...]\n"
            )

    # ----- Queue polling -----------------------------------------------------

    def _poll_queue(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "eta":
                    self._update_eta(data)
                elif kind == "paragraphs_ready":
                    # Only the single-file flow sets review_before_save, so
                    # this can't arrive during a batch run.
                    self._enter_review_mode(data)
                elif kind == "done":
                    if self._batch is not None:
                        self._batch_item_done(data, error=None)
                    else:
                        self._on_done(data)
                elif kind == "error":
                    if self._batch is not None:
                        self._batch_item_done(None, error=data)
                    else:
                        self._on_error(data)
                elif kind == "cancelled":
                    if self._batch is not None:
                        self._batch_cancelled(data)
                    else:
                        self._on_cancelled(data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _update_eta(self, info):
        """Update the progress card with audio progress + ETA."""
        done = info["audio_done"]
        total = info["audio_total"]
        eta = info["eta_seconds"]
        speed = info["speed"]
        pct = (done / total * 100) if total else 0
        self._set_progress(pct)
        self.status_var.set(
            f"{_format_duration(done)} of {_format_duration(total)}   ·   "
            f"about {_format_duration(eta)} remaining   ·   "
            f"{speed:.1f}x audio speed"
        )

    def _set_progress(self, pct):
        """Set the progress bar and percentage label. None blanks both
        (idle); a number pins them (clamped to 0-100)."""
        if pct is None:
            self.progress_bar.config(value=0)
            self.progress_pct_var.set("")
            return
        pct = max(0.0, min(100.0, float(pct)))
        self.progress_bar.config(value=pct)
        self.progress_pct_var.set(f"{pct:.0f}%")

    def _append_log(self, text):
        self.output_text.config(state="normal")
        self.output_text.insert("end", text)
        self.output_text.see("end")
        self.output_text.config(state="disabled")

    def _clear_log(self):
        self.output_text.config(state="normal")
        self.output_text.delete("1.0", "end")
        self.output_text.config(state="disabled")
        self.status_var.set("")
        self._set_progress(None)

    def _on_done(self, output_path):
        self.last_output = output_path
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.open_btn.config(state="normal")
        self.reveal_btn.config(state="normal")
        self.status_var.set("Done")
        self._set_progress(100)
        self._append_log("\n=== Done ===\n")
        if output_path and Path(output_path).exists():
            _recent_add(output_path)
            self._refresh_recent_menu()

    def _on_error(self, message):
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.status_var.set("Failed")
        self.progress_file_var.set("Error")
        self._append_log(f"\n!!! Error !!!\n{message}\n")
        # The full traceback is in the details log; reveal it.
        self._set_details(True)
        # Keep the messagebox short - full error stays in the log.
        first_line = message.splitlines()[0] if message else "Unknown error"
        messagebox.showerror("Error", first_line)

    def _on_cancelled(self, message):
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.status_var.set("Stopped")
        if message:
            self._append_log(f"\n=== Stopped: {message} ===\n")
        else:
            self._append_log("\n=== Stopped ===\n")

    # ----- Review mode -------------------------------------------------------

    def _enter_review_mode(self, info):
        """Hide the main UI and show the ReviewPane with the transcribed
        paragraphs ready for speaker labelling.

        If `info` includes a "preset_speakers" entry (a list of speaker
        name strings or None, parallel to paragraphs) and a
        "preset_speaker_names" mapping (slot letter -> display name),
        we apply them to the pane after construction. This is how the
        load-transcript flow seeds the review pane with pre-existing
        speaker assignments parsed from the saved file."""
        self._pending_review_info = info
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.status_var.set(
            f"Ready for review: {len(info['paragraphs'])} paragraphs"
        )
        self._append_log(
            f"\n{len(info['paragraphs'])} paragraphs ready for review.\n"
        )

        # Crash-safety: persist the un-labelled transcript to disk before
        # entering review. Only for fresh transcriptions (info["result"] is
        # set); loaded transcripts already exist on disk.
        if info.get("result") is not None:
            try:
                write_paragraphs_to_file(
                    info["paragraphs"], Path(info["out_path"]),
                    show_timestamp=info.get("show_timestamp", True),
                    title=info.get("title"),
                    output_format=info["output_format"],
                    speakers=None,
                )
                self._append_log(
                    f"Safety copy saved (no labels yet): {info['out_path']}\n"
                )
            except Exception as e:
                _log(f"Safety save before review failed: {e}",
                     exc_info=sys.exc_info())
                self._append_log(
                    f"Warning: could not save safety copy "
                    f"({type(e).__name__}: {e}). Continuing to review.\n"
                )

        # Hide the main UI (don't destroy - we'll bring it back).
        self.main_frame.pack_forget()

        # Show the review pane in its place.
        loaded = bool(info.get("loaded"))
        pane_cls = (ReviewPaneText if REVIEW_PANE_STYLE == "text"
                    else ReviewPane)
        pane_kwargs = dict(
            on_save=self._on_review_save,
            on_cancel=self._on_review_cancel,
            show_timestamp=info.get("show_timestamp", True),
            loaded=loaded,
            on_save_revision=self._on_review_save_revision if loaded else None,
        )
        # Only the single-Text pane knows about word confidence, audio
        # playback and autosave.
        if pane_cls is ReviewPaneText:
            pane_kwargs["word_conf"] = info.get("word_conf")
            pane_kwargs["audio_path"] = info.get("audio_path")
            pane_kwargs["on_autosave"] = self._on_review_autosave
        self.review_pane = pane_cls(
            self.root,
            info["paragraphs"],
            **pane_kwargs,
        )
        self.review_pane.pack(fill="both", expand=True)

        # Apply preset speaker assignments if the caller provided them
        # (e.g. when loading an existing transcript with speaker labels).
        preset_speakers = info.get("preset_speakers")
        preset_names = info.get("preset_speaker_names")
        if preset_speakers and preset_names:
            self.review_pane.speakers = list(preset_speakers)
            # Reveal enough speaker-name fields to cover the highest slot
            # in use (rebuilds the editor before we populate the entries
            # below). Slot letters are "1".."9" so int() is safe.
            slots = [int(L) for L in preset_names.keys()]
            slots += [int(L) for L in preset_speakers if L]
            if hasattr(self.review_pane, "set_visible_speakers") and slots:
                self.review_pane.set_visible_speakers(max(slots))
            for letter, name in preset_names.items():
                self.review_pane.speaker_names[letter] = name
                if letter in self.review_pane.name_vars:
                    self.review_pane.name_vars[letter].set(name)
            self.review_pane._refresh_all_rows()

    def _refresh_recent_menu(self):
        """Rebuild the File -> Open Recent submenu (and the Recent tab's
        list, once it exists) from disk."""
        self._refresh_recent_tab()
        self.recent_menu.delete(0, "end")
        items = _recent_load()
        items = [p for p in items if Path(p).exists()]
        if not items:
            self.recent_menu.add_command(
                label="(no recent transcripts)", state="disabled")
            return
        for path in items:
            label = Path(path).name
            self.recent_menu.add_command(
                label=label,
                command=lambda p=path: self._open_recent(p),
            )
        self.recent_menu.add_separator()
        self.recent_menu.add_command(
            label="Clear Recent",
            command=self._clear_recent,
        )

    def _open_recent(self, path):
        if not Path(path).exists():
            messagebox.showwarning(
                "File not found",
                f"This file no longer exists:\n{path}",
            )
            self._refresh_recent_menu()
            return
        self._load_transcript(preset_path=path)

    def _clear_recent(self):
        _recent_save([])
        self._refresh_recent_menu()

    def _exit_review_mode(self):
        if self.review_pane is not None:
            self.review_pane.destroy()
            self.review_pane = None
        self.main_frame.pack(fill="both", expand=True)
        # Any exit from review is a deliberate save/discard, so the
        # crash-recovery snapshot is no longer needed.
        _autosave_clear()

    # ----- Review auto-save --------------------------------------------------

    def _on_review_autosave(self, paragraphs, speakers, speaker_names):
        """Persist the in-progress review state for crash recovery.
        Called (debounced) by the review pane after every mutation."""
        info = self._pending_review_info
        if not info:
            return
        # Only keep speaker names that are actually in use or renamed,
        # so restore reveals the right number of name fields.
        defaults = ReviewPaneText.DEFAULT_NAMES
        used = {letter for letter in speakers if letter}
        names = {
            letter: name
            for letter, name in speaker_names.items()
            if letter in used or name != defaults.get(letter)
        }
        _autosave_save({
            "out_path": str(info.get("out_path", "")),
            "show_timestamp": bool(info.get("show_timestamp", True)),
            "title": info.get("title"),
            "output_format": info.get("output_format", "txt"),
            "loaded": bool(info.get("loaded")),
            "audio_path": info.get("audio_path"),
            "paragraphs": [[list(seg) for seg in para]
                           for para in paragraphs],
            "speakers": list(speakers),
            "speaker_names": names,
            "saved_at": time.time(),
        })

    def _maybe_offer_autosave_restore(self):
        """On launch: if a crash left an autosave behind, offer to reopen
        the review session it captured."""
        data = _autosave_load()
        if not data:
            return
        name = Path(data.get("out_path") or "").name or "transcript"
        if not messagebox.askyesno(
            "Restore unsaved review?",
            "Transcribr found a review session that was never saved "
            f"(probably from a crash or force-quit):\n\n{name}\n\n"
            "Restore it now?",
            default="yes",
        ):
            _autosave_clear()
            return
        paragraphs = [
            [tuple(seg) for seg in para] for para in data["paragraphs"]
        ]
        info = {
            "paragraphs": paragraphs,
            "out_path": data.get("out_path", ""),
            "show_timestamp": data.get("show_timestamp", True),
            "title": data.get("title"),
            "output_format": data.get("output_format", "txt"),
            "used_partial": False,
            "result": None,
            "extra_formats": [],
            "loaded": bool(data.get("loaded")),
            "audio_path": data.get("audio_path"),
            "preset_speakers": data.get("speakers") or [],
            "preset_speaker_names": data.get("speaker_names") or {},
        }
        self._append_log(
            f"Restored unsaved review session: {info['out_path']}\n")
        self._enter_review_mode(info)

    def _load_transcript(self, *, preset_path=None):
        """Show a file picker (or use `preset_path` if given), parse the
        chosen transcript, and enter review mode with the parsed content.
        The output path defaults to the same file (so saving overwrites
        it)."""
        if preset_path:
            path = preset_path
        else:
            path = filedialog.askopenfilename(
                title="Open Transcript",
                filetypes=[
                    ("Transcribr files", "*.docx *.txt"),
                    ("Word document", "*.docx"),
                    ("Text file", "*.txt"),
                    ("All files", "*.*"),
                ],
            )
        if not path:
            return
        try:
            parsed = read_paragraphs_from_file(path)
        except TranscriptParseError as e:
            messagebox.showerror("Cannot open transcript", str(e))
            return
        except Exception as e:
            messagebox.showerror(
                "Cannot open transcript",
                f"Unexpected error reading {path}:\n{type(e).__name__}: {e}",
            )
            return

        # Map full speaker names to numbered slots 1..9. We need to
        # collapse possibly-many speaker names back into at most
        # MAX_REVIEW_SPEAKERS slots; if there are more we refuse rather
        # than silently losing distinctions.
        max_speakers = ReviewPaneText.MAX_SPEAKERS
        unique_names = []
        for name in parsed["speakers"]:
            if name and name not in unique_names:
                unique_names.append(name)
        if len(unique_names) > max_speakers:
            messagebox.showerror(
                "Too many speakers",
                f"This transcript contains {len(unique_names)} distinct "
                f"speakers. The review pane only supports up to "
                f"{max_speakers}. Open the file in Word and consolidate the "
                "speaker labels before loading it back into Transcribr.",
            )
            return

        # Build the slot mapping.
        name_to_letter = {}
        preset_speaker_names = {}
        for i, name in enumerate(unique_names):
            letter = str(i + 1)
            name_to_letter[name] = letter
            preset_speaker_names[letter] = name

        # Per-paragraph speaker letters (or None for unlabelled paragraphs).
        preset_speakers = [
            name_to_letter.get(name) if name else None
            for name in parsed["speakers"]
        ]

        # Build the info dict the review pane expects, with the
        # output path defaulting to the file we just loaded so saving
        # overwrites it. The user can change the output path before
        # saving by re-running through the main flow if they want
        # to "save as" a different file.
        info = {
            "paragraphs": parsed["paragraphs"],
            "out_path": path,
            "show_timestamp": parsed["show_timestamp"],
            "title": parsed.get("title"),
            "output_format": "docx" if path.lower().endswith(".docx") else "txt",
            "used_partial": False,
            "result": None,
            "extra_formats": [],
            "preset_speakers": preset_speakers,
            "preset_speaker_names": preset_speaker_names,
            "loaded": True,
            "audio_path": _guess_audio_for_transcript(path),
        }
        self.last_output = path  # so 'Open Output' / 'Show in Folder' work
        _recent_add(path)
        self._refresh_recent_menu()
        self._append_log(f"\nLoaded transcript from: {path}\n")
        self._enter_review_mode(info)

    def _on_review_save(self, paragraphs, speakers):
        """Write the transcript with the user's speaker assignments and edits."""
        info = self._pending_review_info
        if not info:
            return
        out_path = Path(info["out_path"])
        try:
            write_paragraphs_to_file(
                paragraphs, out_path,
                show_timestamp=info["show_timestamp"],
                title=info.get("title"),
                output_format=info["output_format"],
                speakers=speakers,
            )
        except ImportError as e:
            messagebox.showerror("Cannot write file", str(e))
            return
        except Exception as e:
            messagebox.showerror("Save failed",
                                 f"{type(e).__name__}: {e}")
            return

        self._append_log(
            f"Wrote {len(paragraphs)} paragraphs (with speaker labels)\n"
            f"to: {out_path}\n"
        )

        # Extra formats only on full (non-cancelled) runs.
        if info.get("result") and info.get("extra_formats"):
            _write_extra_formats(info["result"], out_path,
                                 info["extra_formats"], self.queue)

        self._exit_review_mode()
        self._pending_review_info = None
        self._on_done(str(out_path))

    def _on_review_save_revision(self, paragraphs, speakers):
        """Write the transcript to a sibling file with a .revN suffix,
        leaving the original on disk untouched. Only used for the
        load-existing-transcript flow."""
        info = self._pending_review_info
        if not info:
            return
        out_path = _next_revision_path(Path(info["out_path"]))
        try:
            write_paragraphs_to_file(
                paragraphs, out_path,
                show_timestamp=info["show_timestamp"],
                title=info.get("title"),
                output_format=info["output_format"],
                speakers=speakers,
            )
        except ImportError as e:
            messagebox.showerror("Cannot write file", str(e))
            return
        except Exception as e:
            messagebox.showerror("Save failed",
                                 f"{type(e).__name__}: {e}")
            return
        self._append_log(
            f"Saved revision: {out_path}\n"
        )
        self.last_output = str(out_path)
        _recent_add(str(out_path))
        self._refresh_recent_menu()
        self._exit_review_mode()
        self._pending_review_info = None
        self._on_done(str(out_path))

    def _on_review_cancel(self):
        """User cancelled review.

        For loaded transcripts: just close — the original file on disk is
        untouched, since the user explicitly chose not to save.

        For fresh transcriptions: save WITHOUT speaker labels so they
        don't lose the transcribed text. (The safety-save in
        _enter_review_mode has already written the same content; we
        re-save here to capture any in-pane edits the user made.)"""
        info = self._pending_review_info
        if not info:
            self._exit_review_mode()
            return

        if info.get("loaded"):
            self._append_log(
                f"Closed without saving: {info['out_path']}\n"
            )
            self._exit_review_mode()
            self._pending_review_info = None
            self._on_done(str(info["out_path"]))
            return

        out_path = Path(info["out_path"])
        # Use the (possibly merged) paragraphs from the review pane if any
        # edits were made; fall back to the original list otherwise.
        paragraphs = (self.review_pane.paragraphs
                      if self.review_pane else info["paragraphs"])
        try:
            write_paragraphs_to_file(
                paragraphs, out_path,
                show_timestamp=info["show_timestamp"],
                title=info.get("title"),
                output_format=info["output_format"],
                speakers=None,
            )
        except ImportError as e:
            messagebox.showerror("Cannot write file", str(e))
            return
        except Exception as e:
            messagebox.showerror("Save failed",
                                 f"{type(e).__name__}: {e}")
            return

        self._append_log(
            f"Saved without speaker labels: {out_path}\n"
        )
        if info.get("result") and info.get("extra_formats"):
            _write_extra_formats(info["result"], out_path,
                                 info["extra_formats"], self.queue)

        self._exit_review_mode()
        self._pending_review_info = None
        self._on_done(str(out_path))

    # ----- Output buttons ----------------------------------------------------

    def _open_output(self):
        if self.last_output and Path(self.last_output).exists():
            _open_path(self.last_output)

    def _reveal_output(self):
        if self.last_output and Path(self.last_output).exists():
            _reveal_path(self.last_output)


# =====================================================================
# Web UI backend - transcript model
# =====================================================================
#
# The review pane's document logic with no widgets: paragraphs (lists
# of (start, end, text) segment tuples), speaker slots "1".."9",
# snapshot-based undo/redo, split/merge/edit/replace-all, playback
# spans, confidence spans, and the debounced autosave trigger. The web
# review surface drives one of these over the API; ReviewPaneText keeps
# its own fused copy of the same logic until the Tk UI is retired.
#
# Semantics are ported 1:1 from ReviewPaneText - where behaviour looks
# arbitrary (the +0.3s playback tail, bailing out of confidence shading
# on alignment drift, empty edit meaning cancel) it is deliberate and
# matches what the Tk pane does today.

class TranscriptModel:
    MAX_SPEAKERS = 9
    DEFAULT_VISIBLE = 4
    LETTERS = tuple(str(i) for i in range(1, 10))
    _UNDO_LIMIT = 200

    def __init__(self, paragraphs, speakers=None, speaker_names=None,
                 word_conf=None, *, on_autosave=None, timer_factory=None):
        self.paragraphs = [list(p) for p in paragraphs]
        n = len(self.paragraphs)
        self.speakers = (list(speakers) if speakers is not None
                         else [None] * n)
        self.speaker_names = {letter: f"Speaker {letter}"
                              for letter in self.LETTERS}
        if speaker_names:
            self.speaker_names.update(speaker_names)
        self.word_conf = word_conf
        self.show_confidence = False
        used = [int(s) for s in self.speakers if s]
        self.visible_speakers = max(self.DEFAULT_VISIBLE,
                                    min(max(used, default=0),
                                        self.MAX_SPEAKERS))
        self._next_id = 0
        self.ids = [self._new_id() for _ in self.paragraphs]
        self.rev = 0
        self._undo_stack = []
        self._redo_stack = []
        self.on_autosave = on_autosave
        self._timer_factory = timer_factory or (
            lambda delay, fn: threading.Timer(delay, fn))
        self._autosave_timer = None

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    # ----- Queries ----------------------------------------------------

    def body(self, idx):
        """The paragraph's rendered text - exactly what the client
        displays and what split()'s character offset indexes into."""
        return " ".join(seg[2] for seg in self.paragraphs[idx]).strip()

    def can_undo(self):
        return bool(self._undo_stack)

    def can_redo(self):
        return bool(self._redo_stack)

    def label_counts(self):
        labelled = sum(1 for s in self.speakers if s is not None)
        return labelled, len(self.speakers)

    def resolved_speakers(self):
        """Slot letters -> stripped display names (None stays None)."""
        return [self.speaker_names.get(letter, "").strip() or None
                if letter else None
                for letter in self.speakers]

    def playback_span(self, idx):
        """(start_seconds, duration_or_None) covering paragraph `idx`,
        or None if there's nothing to play. Loaded transcripts only
        know start times (the parsers synthesise ~1s spans), so a span
        that still looks like a placeholder plays through to the next
        paragraph's start instead - or open-ended for the last one."""
        if not (0 <= idx < len(self.paragraphs)):
            return None
        para = self.paragraphs[idx]
        if not para:
            return None
        start = max(0.0, float(para[0][0]))
        end = float(para[-1][1])
        if end - start <= 1.0:
            if idx + 1 < len(self.paragraphs) and self.paragraphs[idx + 1]:
                next_start = float(self.paragraphs[idx + 1][0][0])
                if next_start > start:
                    return (start, max(0.5, next_start - start + 0.3))
            return (start, None)
        # A touch of tail padding so the last word isn't clipped.
        return (start, max(0.5, end - start + 0.3))

    def _bucket_words_by_paragraph(self):
        """Partition word_conf into one [(word, prob)] list per
        paragraph by end time; single forward pass (both sides are
        time-ordered)."""
        buckets = [[] for _ in self.paragraphs]
        words = self.word_conf or []
        wi = 0
        n_para = len(self.paragraphs)
        for i, para in enumerate(self.paragraphs):
            if not para:
                continue
            p_end = para[-1][1]
            is_last = (i == n_para - 1)
            while wi < len(words):
                if not is_last and words[wi][0] >= p_end:
                    break
                buckets[i].append((words[wi][2], words[wi][3]))
                wi += 1
        return buckets

    def confidence_spans(self):
        """Per paragraph: [(start_char, end_char, "low"|"med"), ...] in
        body() coordinates. A paragraph whose text no longer aligns
        with the captured words gets an empty list - never a wrong
        highlight."""
        if not self.word_conf:
            return [[] for _ in self.paragraphs]
        result = []
        for i, words_i in enumerate(self._bucket_words_by_paragraph()):
            spans = []
            body = self.body(i)
            cursor = 0
            for wtext, prob in words_i:
                token = (wtext or "").strip()
                if not token:
                    continue
                pos = body.find(token, cursor)
                if pos < 0:
                    spans = []      # alignment lost - leave unshaded
                    break
                cursor = pos + len(token)
                if prob is None:
                    continue
                if prob < 0.35:
                    spans.append((pos, cursor, "low"))
                elif prob < 0.6:
                    spans.append((pos, cursor, "med"))
            result.append(spans)
        return result

    def attention_flags(self):
        """True per paragraph when it still needs attention: no speaker,
        or (with confidence shading on) a word below 0.6."""
        buckets = (self._bucket_words_by_paragraph()
                   if (self.show_confidence and self.word_conf) else None)
        flags = []
        for i, letter in enumerate(self.speakers):
            if letter is None:
                flags.append(True)
            elif buckets is not None:
                flags.append(any(p is not None and p < 0.6
                                 for _, p in buckets[i]))
            else:
                flags.append(False)
        return flags

    def _time_at_body_offset(self, para, joined, offset):
        """Start time of the word at/after character `offset` in
        `joined`, from the engine's word timestamps; None when word
        data is missing or no longer aligns with the edited text."""
        if not self.word_conf or not para:
            return None
        p_start = para[0][0]
        p_end = para[-1][1]
        cursor = 0
        for w_start, _w_end, w_text, _prob in self.word_conf:
            if w_start < p_start - 0.001:
                continue
            if w_start > p_end + 0.001:
                break
            token = (w_text or "").strip()
            if not token:
                continue
            pos = joined.find(token, cursor)
            if pos < 0:
                return None
            if pos >= offset:
                return float(w_start)
            cursor = pos + len(token)
        return None

    # ----- Undo / redo / autosave --------------------------------------

    def _snapshot(self):
        return {
            "paragraphs": [list(p) for p in self.paragraphs],
            "speakers": list(self.speakers),
            "speaker_names": dict(self.speaker_names),
            "visible_speakers": self.visible_speakers,
            "ids": list(self.ids),
        }

    def _restore(self, snap):
        self.paragraphs = [list(p) for p in snap["paragraphs"]]
        self.speakers = list(snap["speakers"])
        self.speaker_names = dict(snap["speaker_names"])
        self.visible_speakers = snap["visible_speakers"]
        self.ids = list(snap["ids"])

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        # Every mutation passes through here, so it doubles as the
        # autosave trigger (as in the Tk pane).
        self._schedule_autosave()

    def undo(self):
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self.rev += 1
        self._schedule_autosave()
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self.rev += 1
        self._schedule_autosave()
        return True

    def _schedule_autosave(self):
        """Debounced crash-recovery snapshot: fires 3s after the last
        mutation rather than on every keystroke of a rename."""
        if self.on_autosave is None:
            return
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
        t = self._timer_factory(3.0, self._fire_autosave)
        if hasattr(t, "daemon"):
            t.daemon = True
        t.start()
        self._autosave_timer = t

    def _fire_autosave(self):
        self._autosave_timer = None
        if self.on_autosave is not None:
            self.on_autosave(self.paragraphs, self.speakers,
                             dict(self.speaker_names))

    def flush_autosave(self):
        """Run any pending autosave immediately (shutdown, tests)."""
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._fire_autosave()

    def close(self):
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._autosave_timer = None

    # ----- Mutations ----------------------------------------------------

    def set_speaker(self, idx, letter):
        """Assign slot `letter` ("1".."9") or None to paragraph idx."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        if letter is not None and letter not in self.LETTERS:
            return False
        self._push_undo()
        self.speakers[idx] = letter
        if letter is not None and int(letter) > self.visible_speakers:
            self.set_visible(int(letter))
        self.rev += 1
        return True

    def set_speaker_name(self, letter, name):
        """Rename a slot. No undo step (matches the Tk pane, where each
        keystroke of a rename would otherwise flood the stack), but it
        reschedules the autosave."""
        if letter not in self.LETTERS:
            return False
        self.speaker_names[letter] = name
        self.rev += 1
        self._schedule_autosave()
        return True

    def set_visible(self, n):
        """Show at least `n` speaker-name fields (clamped)."""
        n = max(self.DEFAULT_VISIBLE, min(int(n), self.MAX_SPEAKERS))
        if n != self.visible_speakers:
            self.visible_speakers = n
            self.rev += 1
        return self.visible_speakers

    def commit_edit(self, idx, new_text):
        """Replace paragraph idx's text, collapsing it to one segment
        that keeps the original outer time span. Empty text means
        cancel (no-op); unchanged text records no undo step."""
        if not (0 <= idx < len(self.paragraphs)):
            return False
        new_text = (new_text or "").strip()
        if not new_text:
            return False
        para = self.paragraphs[idx]
        if not para:
            return False
        if new_text != self.body(idx):
            self._push_undo()
        start_t = para[0][0]
        end_t = para[-1][1]
        self.paragraphs[idx] = [(start_t, end_t, new_text)]
        self.rev += 1
        return True

    def merge_with_previous(self, idx):
        if idx is None or idx <= 0 or idx >= len(self.paragraphs):
            return False
        self._push_undo()
        self.paragraphs[idx - 1] = (list(self.paragraphs[idx - 1])
                                    + list(self.paragraphs[idx]))
        del self.paragraphs[idx]
        del self.speakers[idx]
        del self.ids[idx]
        self.rev += 1
        return True

    def split(self, idx, offset):
        """Split paragraphs[idx] at character `offset` within body().
        Both halves get real start/end times: preferring the engine's
        word timestamps, falling back to interpolating by character
        position. The new second half keeps the speaker and gets a
        fresh id. Returns the new paragraph's index, or None."""
        if not (0 <= idx < len(self.paragraphs)):
            return None
        para = self.paragraphs[idx]
        if not para:
            return None
        joined = " ".join(seg[2] for seg in para)
        leading_strip = len(joined) - len(joined.lstrip())
        adjusted_offset = offset + leading_strip
        if adjusted_offset <= 0 or adjusted_offset >= len(joined):
            return None

        seg_starts = []
        running = ""
        for k, (_, _, t) in enumerate(para):
            if k > 0:
                running += " "
            seg_starts.append(len(running))
            running += t

        split_seg = None
        split_within = 0
        for k in range(len(para)):
            seg_start = seg_starts[k]
            seg_end = seg_start + len(para[k][2])
            if adjusted_offset <= seg_start:
                split_seg = k
                split_within = 0
                break
            if adjusted_offset <= seg_end:
                split_seg = k
                split_within = adjusted_offset - seg_start
                break
        if split_seg is None:
            return None

        if split_within == 0:
            first = list(para[:split_seg])
            second = list(para[split_seg:])
        else:
            seg_start_t, seg_end_t, seg_text = para[split_seg]
            text_before = seg_text[:split_within].rstrip()
            text_after = seg_text[split_within:].lstrip()
            split_t = self._time_at_body_offset(para, joined,
                                                adjusted_offset)
            if split_t is None and seg_text:
                split_t = (seg_start_t
                           + (seg_end_t - seg_start_t)
                           * (split_within / len(seg_text)))
            if split_t is None:
                split_t = seg_start_t
            split_t = max(seg_start_t, min(float(split_t), seg_end_t))
            first = list(para[:split_seg])
            if text_before:
                first.append((seg_start_t, split_t, text_before))
            second = []
            if text_after:
                second.append((split_t, seg_end_t, text_after))
            second.extend(para[split_seg + 1:])

        if not first or not second:
            return None

        self._push_undo()
        self.paragraphs[idx] = first
        self.paragraphs.insert(idx + 1, second)
        self.speakers.insert(idx + 1, self.speakers[idx])
        self.ids.insert(idx + 1, self._new_id())
        self.rev += 1
        return idx + 1

    def replace_all(self, term, replacement, match_case=False):
        """Replace every occurrence across the document. One undo step;
        returns the number of replacements (0 = nothing recorded)."""
        if not term:
            return 0
        flags = 0 if match_case else re.IGNORECASE
        pattern = re.compile(re.escape(term), flags)
        total = sum(pattern.subn(lambda _m: replacement, seg[2])[1]
                    for para in self.paragraphs for seg in para)
        if total == 0:
            return 0
        self._push_undo()
        for para in self.paragraphs:
            for k, seg in enumerate(para):
                new_text, n = pattern.subn(lambda _m: replacement, seg[2])
                if n:
                    para[k] = (seg[0], seg[1], new_text)
        self.rev += 1
        return total


def build_worker_params(settings, in_path, out_path, *,
                        review_before_save):
    """Assemble the transcribe_worker params dict from a validated
    settings dict for one (in_path -> out_path) job - the module-level
    twin of WhisperGUI._build_params. Raises _EngineNotAvailable when
    no Whisper engine is installed."""
    if not AVAILABLE_ENGINES:
        raise _EngineNotAvailable(
            "No Whisper engine is installed in this Python.\n\n"
            "Install at least one:\n"
            "  pip install openai-whisper\n"
            "  pip install faster-whisper\n"
            "  pip install mlx-whisper   (Apple Silicon only)\n")

    lang_code = next(
        (c for n, c in LANGUAGES if n == settings["language"]), "en")

    extra_formats = [fmt for fmt in ("json", "srt", "vtt", "tsv")
                     if settings.get(f"extra_{fmt}")]

    # The same text is fed to Whisper as initial_prompt (helps with
    # proper-noun accuracy) and used as the document title.
    description = (settings.get("prompt") or "").strip()

    engine_key = next(
        (k for k, n in AVAILABLE_ENGINES if n == settings["engine"]),
        "whisper")

    return dict(
        input=in_path,
        output=out_path,
        engine=engine_key,
        model=settings["model"],
        language=lang_code,
        task=settings["task"],
        temperature=settings["temperature"],
        beam_size=settings["beam_size"],
        best_of=settings["best_of"],
        compression_ratio_threshold=settings[
            "compression_ratio_threshold"],
        logprob_threshold=settings["logprob_threshold"],
        no_speech_threshold=settings["no_speech_threshold"],
        condition_on_previous_text=settings["condition_on_previous_text"],
        # Confidence highlighting requires word timestamps, so enable
        # them whenever either option is on.
        word_timestamps=(settings["word_timestamps"]
                         or settings["highlight_confidence"]),
        highlight_confidence=settings["highlight_confidence"],
        initial_prompt=description or None,
        # With no description, title the document after the source file
        # (the filename is NOT fed to Whisper as a prompt - recorder
        # names like REC_0042 would only mislead it).
        title=description or Path(in_path).name,
        gap=settings["gap"],
        extra_formats=extra_formats,
        output_format=settings["output_format"],
        show_timestamp=settings["show_timestamp"],
        audio_duration=get_audio_duration(in_path),
        review_before_save=review_before_save,
    )


# =====================================================================
# Web UI backend - settings contract
# =====================================================================
#
# The web front-end shares settings.json with the Tk UI: same keys,
# same value conventions (engine and language are stored as display
# names, exactly as the Tk comboboxes hold them). validate_settings()
# mirrors _apply_settings(): unknown keys and junk values are dropped
# and missing ones fall back to the defaults, so a stale or corrupt
# file can't wedge the app.

_SETTINGS_BOOL_KEYS = (
    "show_timestamp", "review", "condition_on_previous_text",
    "word_timestamps", "extra_json", "extra_srt", "extra_vtt",
    "extra_tsv", "highlight_confidence", "show_details",
)

_SETTINGS_NUMBER_KEYS = (
    "gap", "temperature", "beam_size", "best_of",
    "compression_ratio_threshold", "logprob_threshold",
    "no_speech_threshold",
)


def _settings_choices():
    """Allowed values for every enumerated setting, computed live so
    the engine list reflects what is actually installed."""
    return {
        "engine": [name for _, name in AVAILABLE_ENGINES],
        "model": list(WHISPER_MODELS),
        "language": [n for n, _ in LANGUAGES],
        "task": ["transcribe", "translate"],
        "output_format": ["txt", "docx", "pdf"],
        "theme": ["auto", "light", "dark"],
    }


def default_settings():
    """The out-of-the-box settings - the same values the Tk widgets
    start with."""
    engines = [name for _, name in AVAILABLE_ENGINES]
    return {
        "engine": engines[0] if engines else "",
        "model": "large-v3-turbo",
        "language": "English",
        "task": "transcribe",
        "output_format": "docx",
        "prompt": "",
        "gap": 1.5,
        "show_timestamp": True,
        "review": True,
        "temperature": 0.0,
        "beam_size": 5,
        "best_of": 5,
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": True,
        "word_timestamps": False,
        "extra_json": False,
        "extra_srt": False,
        "extra_vtt": False,
        "extra_tsv": False,
        "highlight_confidence": False,
        "theme": "auto",
        "show_details": False,
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
    for key, allowed in _settings_choices().items():
        v = raw.get(key)
        if isinstance(v, str) and v in allowed:
            merged[key] = v
    v = raw.get("prompt")
    if isinstance(v, str):
        merged["prompt"] = v
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


# =====================================================================
# Web UI backend - event broker (SSE fan-out)
# =====================================================================

class EventBroker:
    """Fans server-side events out to any number of SSE subscribers.

    Events carry monotonically increasing ids and are kept in a bounded
    ring so a reconnecting client (Last-Event-ID) can replay what it
    missed; when the gap is wider than the ring the subscriber is told
    to resync via GET /api/state instead."""

    _RING_SIZE = 500

    def __init__(self):
        from collections import deque
        self._lock = threading.Lock()
        self._seq = 0
        self._subscribers = set()
        self._ring = deque(maxlen=self._RING_SIZE)

    def publish(self, event, data):
        import json
        with self._lock:
            self._seq += 1
            item = (self._seq, event, json.dumps(data))
            self._ring.append(item)
            targets = list(self._subscribers)
        for q in targets:
            q.put(item)

    def subscribe(self, last_event_id=None):
        """Register a subscriber. Returns (queue, backlog): backlog is
        the list of missed events, or None when the gap can't be
        bridged and the client must do a full resync."""
        q = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
            if last_event_id is None:
                return q, []
            ring = list(self._ring)
        if not ring or last_event_id < ring[0][0] - 1:
            return q, None
        return q, [item for item in ring if item[0] > last_event_id]

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)


def _sse_format(item):
    seq, event, payload = item
    return f"id: {seq}\nevent: {event}\ndata: {payload}\n\n"


# =====================================================================
# Web UI backend - run controller
# =====================================================================

class ApiFail(Exception):
    """A request failure with an HTTP status and machine-readable code;
    the route layer turns it into a JSON error response."""

    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra


_NO_ENGINE_HINT = (
    "No Whisper engine is installed in this Python.\n\n"
    "Install at least one:\n"
    "  pip install openai-whisper\n"
    "  pip install faster-whisper\n"
    "  pip install mlx-whisper   (Apple Silicon only)")


class RunController:
    """Single-run and batch state machine - the module-level twin of
    WhisperGUI's run/batch/stop handlers. Mutating entry points raise
    ApiFail on validation problems; progress flows out as SSE events
    via the broker. `worker_fn` is injectable for tests."""

    _LOG_CAP = 4000          # lines kept for late-joining clients

    def __init__(self, broker, *, worker_fn=None):
        self.broker = broker
        self.queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.worker_fn = worker_fn or transcribe_worker
        self.lock = threading.RLock()
        self._batch = None
        self.phase = "idle"    # idle|running|stopping|done|error|cancelled
        self.current_file = None
        self.run_id = 0
        self.last_output = None
        self.log_lines = []
        self.progress = None
        # Phase 3 sets this to route paragraphs_ready into a review
        # session; until then runs force review_before_save=False.
        self.on_paragraphs_ready = None

    # ----- Snapshots ----------------------------------------------------

    def state(self):
        with self.lock:
            batch = None
            if self._batch is not None:
                batch = {"index": self._batch["index"],
                         "total": len(self._batch["items"])}
            return {
                "phase": self.phase,
                "file": self.current_file,
                "run_id": self.run_id,
                "batch": batch,
                "out_path": self.last_output,
                "progress": self.progress,
                "log": "".join(self.log_lines)[-40000:],
            }

    def _publish_run_state(self, **extra):
        self.broker.publish("run_state", {**{
            "phase": self.phase,
            "file": self.current_file,
            "run_id": self.run_id,
            "batch": (None if self._batch is None else
                      {"index": self._batch["index"],
                       "total": len(self._batch["items"])}),
            "out_path": self.last_output,
        }, **extra})

    def _append_log(self, text):
        self.log_lines.append(text)
        if len(self.log_lines) > self._LOG_CAP:
            del self.log_lines[:len(self.log_lines) - self._LOG_CAP]
        self.broker.publish("log", {"text": text})

    def _reset_log(self):
        self.log_lines = []

    # ----- Starting work -------------------------------------------------

    def _ensure_idle(self):
        if self.phase in ("running", "stopping"):
            raise ApiFail(409, "busy", "A transcription is already "
                                       "running.")

    def _spawn(self, params):
        self.cancel_event.clear()
        self.worker = threading.Thread(
            target=self.worker_fn,
            args=(params, self.queue, self.cancel_event),
            daemon=True)
        self.worker.start()

    def start_single(self, in_path, out_path, settings, *, force=False):
        """Validate and launch one transcription. Returns the new
        run_id. Mirrors WhisperGUI._on_run."""
        with self.lock:
            self._ensure_idle()
            in_path = (in_path or "").strip()
            if not in_path:
                raise ApiFail(400, "missing_input",
                              "Please choose an input audio/video file, "
                              "or add files to the batch queue.")
            if not Path(in_path).exists():
                raise ApiFail(400, "input_not_found",
                              f"Input file does not exist:\n{in_path}",
                              path=in_path)
            out_path = (out_path or "").strip()
            if not out_path:
                ext = settings["output_format"]
                out_path = str(Path(in_path).with_suffix(
                    f".transcript.{ext}"))
            if Path(out_path).exists() and not force:
                raise ApiFail(409, "output_exists",
                              f"The output file already exists:\n\n"
                              f"{out_path}", path=out_path)
            try:
                params = build_worker_params(
                    settings, in_path, out_path,
                    review_before_save=bool(settings.get("review")))
            except _EngineNotAvailable:
                raise ApiFail(409, "no_engine", _NO_ENGINE_HINT)

            self._batch = None
            self.run_id += 1
            self.phase = "running"
            self.current_file = Path(in_path).name
            self.last_output = None
            self.progress = None
            self._reset_log()
            self._publish_run_state()
            self._append_log(f"=== {Path(in_path).name} ===\n")
            self._spawn(params)
            return self.run_id

    def start_batch(self, files, settings, *, force=False):
        """Validate and launch a sequential unattended batch (never
        reviews). Mirrors WhisperGUI._start_batch."""
        with self.lock:
            self._ensure_idle()
            files = [f for f in (files or []) if (f or "").strip()]
            if not files:
                raise ApiFail(400, "missing_input",
                              "The batch queue is empty.")
            ext = settings["output_format"]
            items = []
            missing = []
            for in_path in files:
                if not Path(in_path).exists():
                    missing.append(in_path)
                    continue
                items.append((in_path, str(Path(in_path).with_suffix(
                    f".transcript.{ext}"))))
            if missing:
                raise ApiFail(400, "missing_inputs",
                              "These queued files no longer exist.",
                              missing=missing)
            existing = [o for _, o in items if Path(o).exists()]
            if existing and not force:
                raise ApiFail(409, "outputs_exist",
                              f"{len(existing)} output file(s) already "
                              "exist and will be overwritten.",
                              existing=existing[:8], total=len(existing))
            try:
                # Probe once so an engine problem surfaces before any
                # file is committed to the run.
                build_worker_params(settings, items[0][0], items[0][1],
                                    review_before_save=False)
            except _EngineNotAvailable:
                raise ApiFail(409, "no_engine", _NO_ENGINE_HINT)

            self._batch = {"items": items, "index": 0, "settings":
                           dict(settings), "succeeded": [], "failed": [],
                           "stop": False}
            self.run_id += 1
            self.last_output = None
            self.progress = None
            self._reset_log()
            self._append_log(
                f"=== Batch: {len(items)} file(s) queued ===\n")
            self._start_batch_item()
            return self.run_id

    def _start_batch_item(self):
        b = self._batch
        if b is None:
            return
        idx = b["index"]
        in_path, out_path = b["items"][idx]
        n = len(b["items"])
        self._append_log(
            f"\n--- File {idx + 1} of {n}: {Path(in_path).name} ---\n")
        self.phase = "running"
        self.current_file = f"{Path(in_path).name}  (file {idx + 1} of {n})"
        self.progress = None
        try:
            params = build_worker_params(b["settings"], in_path, out_path,
                                         review_before_save=False)
        except _EngineNotAvailable:
            # Engine vanished mid-run; abort the batch.
            self._finish_batch(stopped=True)
            return
        self._publish_run_state()
        self._spawn(params)

    def stop(self):
        """Stop after the current segment (single) / current file
        (batch), saving the partial transcript. Mirrors _on_stop."""
        with self.lock:
            if self._batch is not None:
                self._batch["stop"] = True
            if self.worker and self.worker.is_alive():
                self.cancel_event.set()
                self.phase = "stopping"
                self._append_log(
                    "\n[Stop requested - finishing current segment "
                    "and saving partial transcript...]\n")
                self._publish_run_state()
                return True
            return False

    # ----- Worker-queue dispatch (called from the pump thread) -----------

    def handle_message(self, kind, data):
        with self.lock:
            if kind == "log":
                self._append_log(data)
            elif kind == "eta":
                self._handle_eta(data)
            elif kind == "paragraphs_ready":
                if self.on_paragraphs_ready is not None:
                    self.on_paragraphs_ready(data)
                else:
                    self._append_log("\n[Internal: review payload arrived "
                                     "with no review surface]\n")
            elif kind == "done":
                if self._batch is not None:
                    self._batch_item_done(data, error=None)
                else:
                    self._on_done(data)
            elif kind == "error":
                if self._batch is not None:
                    self._batch_item_done(None, error=data)
                else:
                    self._on_error(data)
            elif kind == "cancelled":
                if self._batch is not None:
                    self._batch_cancelled(data)
                else:
                    self._on_cancelled(data)

    def _handle_eta(self, info):
        done = info["audio_done"]
        total = info["audio_total"]
        pct = (done / total * 100) if total else 0
        self.progress = {
            "pct": max(0.0, min(100.0, float(pct))),
            "status_text": (
                f"{_format_duration(done)} of {_format_duration(total)}"
                f"   ·   about {_format_duration(info['eta_seconds'])} "
                f"remaining   ·   {info['speed']:.1f}x audio speed"),
            **info,
        }
        self.broker.publish("progress", self.progress)

    def _on_done(self, output_path):
        self.phase = "done"
        self.last_output = output_path
        self.progress = {"pct": 100.0, "status_text": "Done"}
        self.broker.publish("progress", self.progress)
        self._append_log("\n=== Done ===\n")
        if output_path and Path(output_path).exists():
            _recent_add(output_path)
            self.broker.publish("recents", {})
        self._publish_run_state()

    def _on_error(self, message):
        self.phase = "error"
        first_line = (message.splitlines()[0] if message
                      else "Unknown error")
        self._append_log(f"\n!!! Error !!!\n{message}\n")
        self._publish_run_state(message=message, first_line=first_line)

    def _on_cancelled(self, message):
        self.phase = "cancelled"
        if message:
            self._append_log(f"\n=== Stopped: {message} ===\n")
        else:
            self._append_log("\n=== Stopped ===\n")
        self._publish_run_state()

    def _batch_item_done(self, output_path, error):
        b = self._batch
        if b is None:
            return
        idx = b["index"]
        in_path = b["items"][idx][0]
        if error is not None:
            first_line = (error.splitlines()[0] if error
                          else "Unknown error")
            b["failed"].append([in_path, first_line])
            self._append_log(
                f"FAILED: {Path(in_path).name}: {first_line}\n")
        else:
            b["succeeded"].append(output_path)
            if output_path and Path(output_path).exists():
                _recent_add(output_path)
                self.broker.publish("recents", {})
        if b["stop"]:
            self._finish_batch(stopped=True)
            return
        b["index"] += 1
        if b["index"] < len(b["items"]):
            self._start_batch_item()
        else:
            self._finish_batch(stopped=False)

    def _batch_cancelled(self, message):
        if self._batch is None:
            return
        if message:
            self._append_log(f"\n=== Stopped: {message} ===\n")
        self._finish_batch(stopped=True)

    def _finish_batch(self, *, stopped):
        b = self._batch
        self._batch = None
        succeeded = b["succeeded"] if b else []
        failed = b["failed"] if b else []
        if succeeded:
            self.last_output = succeeded[-1]
        head = "Batch stopped" if stopped else "Batch complete"
        self.phase = "cancelled" if stopped else "done"
        self.current_file = head
        lines = [f"\n=== {head} ===",
                 f"Transcribed: {len(succeeded)}",
                 f"Failed: {len(failed)}"]
        for in_path, why in failed:
            lines.append(f"  - {Path(in_path).name}: {why}")
        lines.append("Open each transcript from the Library to review "
                     "and label speakers.")
        self._append_log("\n".join(lines) + "\n")
        self.broker.publish("batch_done", {
            "stopped": stopped,
            "succeeded": succeeded,
            "failed": failed,
        })
        self._publish_run_state()


# =====================================================================
# Web UI backend - audio preparation for playback
# =====================================================================
#
# The review pane's <audio> element needs a source both webviews can
# actually play and seek: mp3/m4a/aac/wav are served as-is; anything
# else (video containers, flac/ogg/opus) is extracted once to AAC in
# an .m4a, cached under the config dir keyed by (path, size, mtime).
# When the source's audio stream is already AAC the extraction is a
# near-instant remux (-c:a copy). WKWebView refuses media from servers
# without Range support, which is why /audio/current is served through
# bottle's static_file.

_AUDIO_PASSTHROUGH_EXTS = {".mp3", ".m4a", ".aac", ".wav"}
_AUDIO_CACHE_MAX_FILES = 8
_AUDIO_CACHE_MAX_BYTES = 1_000_000_000


def _audio_cache_dir():
    d = _config_dir() / "audio_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep_audio_cache():
    """Keep the newest few extracts within the size budget."""
    try:
        files = sorted(_audio_cache_dir().glob("*.m4a"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for i, p in enumerate(files):
            total += p.stat().st_size
            if i >= _AUDIO_CACHE_MAX_FILES or total > _AUDIO_CACHE_MAX_BYTES:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _audio_cache_key(path):
    import hashlib
    st = Path(path).stat()
    raw = f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _source_audio_codec(path):
    """The first audio stream's codec name, or None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0",
             str(path)],
            capture_output=True, text=True, timeout=30)
        codec = (out.stdout or "").strip()
        return codec or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class AudioPrep:
    """Prepares one review session's audio for the <audio> element on a
    background thread, reporting progress as audio_status SSE events.
    States: probing -> extracting -> ready | unavailable."""

    def __init__(self, audio_path, broker):
        self.source = audio_path
        self.broker = broker
        self.state = "unavailable" if not audio_path else "probing"
        self.serve_path = None
        self.duration = None
        self.error = None
        if audio_path:
            threading.Thread(target=self._run, daemon=True,
                             name="transcribr-audio").start()

    def status(self):
        out = {"state": self.state}
        if self.state == "ready":
            out["url"] = "/audio/current"
            out["duration"] = self.duration
        if self.error:
            out["error"] = self.error
        return out

    def _set(self, state, error=None):
        self.state = state
        self.error = error
        self.broker.publish("audio_status", self.status())

    def _run(self):
        try:
            src = Path(self.source)
            if not src.exists():
                self._set("unavailable", "Source audio not found.")
                return
            self.duration = get_audio_duration(str(src))
            if src.suffix.lower() in _AUDIO_PASSTHROUGH_EXTS:
                self.serve_path = str(src)
                self._set("ready")
                return
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self._set("unavailable",
                          "ffmpeg is needed to play this file type.")
                return
            target = _audio_cache_dir() / f"{_audio_cache_key(src)}.m4a"
            if target.exists():
                self.serve_path = str(target)
                self._set("ready")
                return
            self._set("extracting")
            codec_args = (["-c:a", "copy"]
                          if _source_audio_codec(src) == "aac"
                          else ["-c:a", "aac", "-b:a", "128k"])
            tmp = target.with_suffix(".part.m4a")
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(src), "-vn", "-map", "0:a:0",
                   *codec_args, str(tmp)]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=1800)
            if proc.returncode != 0 or not tmp.exists():
                detail = (proc.stderr or "").strip().splitlines()
                self._set("unavailable",
                          detail[-1] if detail else "Extraction failed.")
                tmp.unlink(missing_ok=True)
                return
            os.replace(tmp, target)
            _sweep_audio_cache()
            self.serve_path = str(target)
            self._set("ready")
        except Exception as e:
            _log(f"audio prep failed: {traceback.format_exc()}")
            self._set("unavailable", f"{type(e).__name__}: {e}")


# =====================================================================
# Web UI backend - review session
# =====================================================================

def open_transcript_info(path):
    """Parse a saved transcript into the review-session info dict - the
    module-level twin of WhisperGUI._load_transcript's parsing half.
    Raises ApiFail for parse errors and the >9-speakers refusal."""
    try:
        parsed = read_paragraphs_from_file(path)
    except TranscriptParseError as e:
        raise ApiFail(400, "parse_error", str(e))
    except Exception as e:
        raise ApiFail(400, "parse_error",
                      f"Unexpected error reading {path}:\n"
                      f"{type(e).__name__}: {e}")

    max_speakers = TranscriptModel.MAX_SPEAKERS
    unique_names = []
    for name in parsed["speakers"]:
        if name and name not in unique_names:
            unique_names.append(name)
    if len(unique_names) > max_speakers:
        raise ApiFail(
            422, "too_many_speakers",
            f"This transcript contains {len(unique_names)} distinct "
            f"speakers. The review pane only supports up to "
            f"{max_speakers}. Open the file in Word and consolidate the "
            "speaker labels before loading it back into Transcribr.",
            count=len(unique_names), max=max_speakers)

    name_to_letter = {}
    preset_speaker_names = {}
    for i, name in enumerate(unique_names):
        letter = str(i + 1)
        name_to_letter[name] = letter
        preset_speaker_names[letter] = name
    preset_speakers = [name_to_letter.get(name) if name else None
                       for name in parsed["speakers"]]

    return {
        "paragraphs": parsed["paragraphs"],
        "out_path": path,
        "show_timestamp": parsed["show_timestamp"],
        "title": parsed.get("title"),
        "output_format": ("docx" if path.lower().endswith(".docx")
                          else "txt"),
        "used_partial": False,
        "result": None,
        "extra_formats": [],
        "preset_speakers": preset_speakers,
        "preset_speaker_names": preset_speaker_names,
        "loaded": True,
        "audio_path": _guess_audio_for_transcript(path),
    }


def autosave_restore_info(data):
    """Rebuild the review info dict from an autosave.json payload - the
    twin of _maybe_offer_autosave_restore's restore half."""
    paragraphs = [[tuple(seg) for seg in para]
                  for para in data["paragraphs"]]
    return {
        "paragraphs": paragraphs,
        "out_path": data.get("out_path", ""),
        "show_timestamp": data.get("show_timestamp", True),
        "title": data.get("title"),
        "output_format": data.get("output_format", "txt"),
        "used_partial": False,
        "result": None,
        "extra_formats": [],
        "loaded": bool(data.get("loaded")),
        "audio_path": data.get("audio_path"),
        "preset_speakers": data.get("speakers") or [],
        "preset_speaker_names": data.get("speaker_names") or {},
    }


class ReviewSession:
    """One open review: a TranscriptModel plus document metadata and
    the save/close orchestration - the web twin of WhisperGUI's review
    host code. Mutations must present the client's last-seen model rev;
    a mismatch raises ApiFail 409 so a stale window can't clobber the
    session."""

    def __init__(self, info, broker, *, log=None):
        self.broker = broker
        self.info = info
        self.out_path = str(info.get("out_path", ""))
        self.show_timestamp = bool(info.get("show_timestamp", True))
        self.title = info.get("title")
        self.output_format = info.get("output_format", "txt")
        self.loaded = bool(info.get("loaded"))
        self.audio_path = info.get("audio_path")
        self.result = info.get("result")
        self.extra_formats = info.get("extra_formats") or []
        self._log_line = log or (lambda text: None)
        self.lock = threading.RLock()
        self.closed = False
        self.audio_status_fn = None   # set by the backend (AudioPrep)
        preset_speakers = info.get("preset_speakers") or None
        self.model = TranscriptModel(
            info["paragraphs"],
            speakers=preset_speakers,
            speaker_names=info.get("preset_speaker_names") or None,
            word_conf=info.get("word_conf"),
            on_autosave=self._write_autosave,
        )
        # Reveal enough name fields to cover preset slots (parity with
        # _enter_review_mode's set_visible_speakers call).
        names = info.get("preset_speaker_names") or {}
        slots = ([int(L) for L in names.keys()]
                 + [int(L) for L in (preset_speakers or []) if L])
        if slots:
            self.model.set_visible(max(slots))

    @classmethod
    def from_fresh(cls, info, broker, *, log=None):
        """Session for a fresh transcription: crash-safety pre-save of
        the un-labelled transcript before review opens (port of
        _enter_review_mode lines 5992-6010)."""
        session = cls(info, broker, log=log)
        if info.get("result") is not None:
            try:
                write_paragraphs_to_file(
                    info["paragraphs"], Path(info["out_path"]),
                    show_timestamp=info.get("show_timestamp", True),
                    title=info.get("title"),
                    output_format=info["output_format"],
                    speakers=None,
                )
                session._log_line(
                    f"Safety copy saved (no labels yet): "
                    f"{info['out_path']}\n")
            except Exception as e:
                _log(f"Safety save before review failed: {e}")
                session._log_line(
                    f"Warning: could not save safety copy "
                    f"({type(e).__name__}: {e}). Continuing to review.\n")
        return session

    # ----- Payload -------------------------------------------------------

    def payload(self):
        with self.lock:
            m = self.model
            conf = m.confidence_spans()
            paragraphs = []
            for i, para in enumerate(m.paragraphs):
                span = m.playback_span(i)
                if span is None:
                    play = None
                else:
                    start, dur = span
                    play = {"start": start,
                            "end": None if dur is None else start + dur}
                paragraphs.append({
                    "id": m.ids[i],
                    "start": para[0][0] if para else 0.0,
                    "end": para[-1][1] if para else 0.0,
                    "body": m.body(i),
                    "speaker": m.speakers[i],
                    "play": play,
                    "conf": [list(s) for s in conf[i]],
                })
            labelled, total = m.label_counts()
            return {
                "rev": m.rev,
                "out_path": self.out_path,
                "output_format": self.output_format,
                "show_timestamp": self.show_timestamp,
                "title": self.title,
                "loaded": self.loaded,
                "audio": (self.audio_status_fn()
                          if self.audio_status_fn is not None
                          else {"state": "unavailable"}),
                "speaker_names": dict(m.speaker_names),
                "visible_speakers": m.visible_speakers,
                "labelled": labelled,
                "total": total,
                "can_undo": m.can_undo(),
                "can_redo": m.can_redo(),
                "has_word_conf": bool(m.word_conf),
                "paragraphs": paragraphs,
            }

    def _slim(self, **extra):
        labelled, total = self.model.label_counts()
        return {"rev": self.model.rev, "labelled": labelled,
                "total": total,
                "visible_speakers": self.model.visible_speakers, **extra}

    # ----- Mutations ------------------------------------------------------

    def _check_rev(self, rev):
        if rev != self.model.rev:
            raise ApiFail(409, "stale_rev",
                          "The document changed under you - refetch.",
                          rev=self.model.rev)

    def mutate(self, rev, action, body):
        """Apply one named mutation; returns a slim delta for hot ops
        and the full payload for structural ones."""
        with self.lock:
            self._check_rev(rev)
            m = self.model
            if action == "speaker":
                idx = int(body.get("index", -1))
                letter = body.get("slot")
                if not m.set_speaker(idx, letter):
                    raise ApiFail(400, "bad_request", "Bad index or slot.")
                result = self._slim(index=idx, speaker=letter)
            elif action == "speaker-name":
                if not m.set_speaker_name(str(body.get("slot")),
                                          str(body.get("name", ""))):
                    raise ApiFail(400, "bad_request", "Bad slot.")
                result = self._slim(speaker_names=dict(m.speaker_names))
            elif action == "visible-speakers":
                m.set_visible(int(body.get("n", m.DEFAULT_VISIBLE)))
                result = self._slim()
            elif action == "edit":
                m.commit_edit(int(body.get("index", -1)),
                              str(body.get("text", "")))
                result = self.payload()
            elif action == "split":
                new_idx = m.split(int(body.get("index", -1)),
                                  int(body.get("offset", -1)))
                result = self.payload()
                result["new_index"] = new_idx
            elif action == "merge":
                if not m.merge_with_previous(int(body.get("index", -1))):
                    raise ApiFail(400, "bad_request",
                                  "Can't merge the first paragraph.")
                result = self.payload()
            elif action == "replace-all":
                count = m.replace_all(str(body.get("find", "")),
                                      str(body.get("replace", "")),
                                      bool(body.get("match_case")))
                result = self.payload()
                result["count"] = count
            elif action == "undo":
                m.undo()
                result = self.payload()
            elif action == "redo":
                m.redo()
                result = self.payload()
            else:
                raise ApiFail(404, "unknown_action", action)
            self.broker.publish("review_changed", {"rev": m.rev})
            return result

    # ----- Autosave -------------------------------------------------------

    def _write_autosave(self, paragraphs, speakers, speaker_names):
        """Crash-recovery snapshot in the exact v0.6.0 schema (port of
        _on_review_autosave, incl. the used-or-renamed names filter)."""
        if self.closed:
            return
        defaults = {L: f"Speaker {L}" for L in TranscriptModel.LETTERS}
        used = {letter for letter in speakers if letter}
        names = {letter: name for letter, name in speaker_names.items()
                 if letter in used or name != defaults.get(letter)}
        _autosave_save({
            "out_path": self.out_path,
            "show_timestamp": self.show_timestamp,
            "title": self.title,
            "output_format": self.output_format,
            "loaded": self.loaded,
            "audio_path": self.audio_path,
            "paragraphs": [[list(seg) for seg in para]
                           for para in paragraphs],
            "speakers": list(speakers),
            "speaker_names": names,
            "saved_at": time.time(),
        })
        self.broker.publish("autosave", {"saved_at": time.time()})

    # ----- Save / close ----------------------------------------------------

    def _write(self, out_path, speakers):
        try:
            write_paragraphs_to_file(
                self.model.paragraphs, Path(out_path),
                show_timestamp=self.show_timestamp,
                title=self.title,
                output_format=self.output_format,
                speakers=speakers,
            )
        except ImportError as e:
            raise ApiFail(500, "missing_dependency", str(e))
        except ApiFail:
            raise
        except Exception as e:
            raise ApiFail(500, "save_failed", f"{type(e).__name__}: {e}")

    def save(self, rev, mode, extra_queue=None):
        """mode: "labels" | "no_labels" | "revision". Returns the final
        out_path. Ports _on_review_save / _on_review_cancel(fresh) /
        _on_review_save_revision."""
        with self.lock:
            self._check_rev(rev)
            m = self.model
            for letter in m.LETTERS:
                m.speaker_names[letter] = m.speaker_names.get(
                    letter, "").strip() or f"Speaker {letter}"
            if mode == "revision":
                if not self.loaded:
                    raise ApiFail(400, "bad_request",
                                  "Revisions only apply to loaded "
                                  "transcripts.")
                out_path = str(_next_revision_path(Path(self.out_path)))
                self._write(out_path, m.resolved_speakers())
                self._log_line(f"Saved revision: {out_path}\n")
            elif mode == "labels":
                out_path = self.out_path
                self._write(out_path, m.resolved_speakers())
                self._log_line(
                    f"Wrote {len(m.paragraphs)} paragraphs (with speaker "
                    f"labels)\nto: {out_path}\n")
            elif mode == "no_labels":
                out_path = self.out_path
                self._write(out_path, None)
                self._log_line(f"Saved without speaker labels: "
                               f"{out_path}\n")
            else:
                raise ApiFail(400, "bad_request", f"Unknown mode {mode}.")

            # Extra formats only accompany full fresh runs (parity).
            if (mode in ("labels", "no_labels") and self.result
                    and self.extra_formats and extra_queue is not None):
                _write_extra_formats(self.result, Path(out_path),
                                     self.extra_formats, extra_queue)
            self._finish(reason="saved", out_path=out_path)
            return out_path

    def close_discard(self):
        """Close a LOADED session without saving (the original file is
        untouched). Fresh sessions use save(mode="no_labels") instead."""
        with self.lock:
            if not self.loaded:
                raise ApiFail(400, "bad_request",
                              "A fresh transcription must be saved "
                              "(with or without labels).")
            self._log_line(f"Closed without saving: {self.out_path}\n")
            self._finish(reason="discarded", out_path=None)

    def _finish(self, *, reason, out_path):
        self.closed = True
        self.model.close()
        _autosave_clear()
        if out_path:
            _recent_add(out_path)
            self.broker.publish("recents", {})
        self.broker.publish("review_closed",
                            {"reason": reason, "out_path": out_path})


# =====================================================================
# Web UI backend - HTTP server (bottle on a threading WSGI server)
# =====================================================================
#
# The web UI is served entirely from a loopback-only HTTP server: the
# static front-end build (webdist/), a JSON API, and a Server-Sent
# Events stream. Every /api/ and /audio/ request must present the
# per-session token - header X-Transcribr-Token, or ?token= for the
# two GETs that cannot set headers (EventSource and <audio>). bottle
# is imported lazily so `import transcribr` works without it.

def _webdist_dir():
    return Path(__file__).resolve().parent / "webdist"


class WebBackend:
    """Everything the web route handlers need, plus server plumbing."""

    def __init__(self, token):
        self.token = token
        self.broker = EventBroker()
        self.controller = RunController(self.broker)
        self.controller.on_paragraphs_ready = self._open_review_fresh
        self.review = None        # the open ReviewSession, if any
        self.audio = None         # AudioPrep for the open session
        self.window = None        # pywebview window (web mode only)
        self.server = None        # set by serve()
        self.has_window = False   # True once a pywebview window owns us
        self._pump_stop = threading.Event()

    def _open_review_fresh(self, info):
        """paragraphs_ready arrived from the worker (review-before-save
        run): build the session and tell every client. Runs on the pump
        thread."""
        self.controller.phase = "idle"
        self.controller._append_log(
            f"\n{len(info['paragraphs'])} paragraphs ready for review.\n")
        self.controller._publish_run_state()
        session = ReviewSession.from_fresh(
            info, self.broker, log=self.controller._append_log)
        self._attach_audio(session)
        self.review = session
        self.broker.publish("review_opened", {"review": session.payload()})

    def _open_review(self, info):
        """Open a session for a loaded transcript or autosave restore."""
        session = ReviewSession(info, self.broker,
                                log=self.controller._append_log)
        self._attach_audio(session)
        self.review = session
        self.broker.publish("review_opened", {"review": session.payload()})
        return session

    def _attach_audio(self, session):
        self.audio = AudioPrep(session.audio_path, self.broker)
        session.audio_status_fn = self.audio.status

    def _live_review(self):
        session = self.review
        if session is None or session.closed:
            raise ApiFail(404, "no_review", "No review session is open.")
        return session

    def start_pump(self):
        """Drain the worker queue on a daemon thread, dispatching into
        the controller (whose handlers publish SSE) - the web
        replacement for WhisperGUI._poll_queue's after() loop."""
        def pump():
            while not self._pump_stop.is_set():
                try:
                    kind, data = self.controller.queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self.controller.handle_message(kind, data)
                except Exception:
                    _log("web pump error:\n" + traceback.format_exc())
        threading.Thread(target=pump, daemon=True,
                         name="transcribr-pump").start()

    # -- routes ---------------------------------------------------------

    def build_app(self):
        import json
        import bottle

        app = bottle.Bottle()
        backend = self

        @app.hook("before_request")
        def _guard():
            path = bottle.request.path
            if path.startswith("/api/") or path.startswith("/audio/"):
                supplied = (bottle.request.get_header("X-Transcribr-Token")
                            or bottle.request.query.get("token"))
                if supplied != backend.token:
                    raise bottle.HTTPResponse(
                        body=json.dumps({"error": {
                            "code": "unauthorized",
                            "message": "Missing or bad token."}}),
                        status=401,
                        headers={"Content-Type": "application/json"})

        # -- static shell ------------------------------------------------

        @app.get("/")
        def index():
            root = _webdist_dir()
            if not (root / "index.html").exists():
                bottle.response.content_type = "text/html"
                return ("<h1>Transcribr</h1><p>The web interface has not "
                        "been built yet. From the repository, run:</p>"
                        "<pre>cd web && npm install && npm run build</pre>")
            return bottle.static_file("index.html", root=str(root))

        @app.get("/assets/<filepath:path>")
        def assets(filepath):
            return bottle.static_file(
                filepath, root=str(_webdist_dir() / "assets"))

        # -- meta / state ------------------------------------------------

        @app.get("/api/meta")
        def api_meta():
            return {
                "version": __version__,
                "about_text": ABOUT_TEXT,
                "platform": sys.platform,
                "reveal_label": _REVEAL_LABEL,
                "ui_mode": "webview" if backend.has_window else "browser",
                "engines": [{"key": k, "name": n}
                            for k, n in AVAILABLE_ENGINES],
                "models": list(WHISPER_MODELS),
                "languages": [[n, c] for n, c in LANGUAGES],
                "palettes": _PALETTES,
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "readme_available": _find_readme() is not None,
            }

        def _fail(e):
            raise bottle.HTTPResponse(
                body=json.dumps({"error": {"code": e.code,
                                           "message": str(e),
                                           **e.extra}}),
                status=e.status,
                headers={"Content-Type": "application/json"})

        def _recents_payload():
            recents = []
            for p in _recent_load():
                try:
                    exists = Path(p).exists()
                except OSError:
                    exists = False
                if exists:
                    recents.append({"path": p, "name": Path(p).name,
                                    "exists": True})
            return recents

        @app.get("/api/state")
        def api_state():
            review = None
            if backend.review is not None and not backend.review.closed:
                review = backend.review.payload()
            return {
                "run": backend.controller.state(),
                "review": review,
                "autosave_pending": bool(_autosave_load()),
                "recents": _recents_payload(),
            }

        # -- review --------------------------------------------------------

        @app.get("/api/review")
        def api_review():
            try:
                return backend._live_review().payload()
            except ApiFail as e:
                _fail(e)

        @app.post("/api/transcripts/open")
        def api_transcripts_open():
            body = bottle.request.json or {}
            path = (body.get("path") or "").strip()
            try:
                if backend.review is not None and not backend.review.closed:
                    raise ApiFail(409, "review_open",
                                  "A review session is already open.")
                if backend.controller.phase in ("running", "stopping"):
                    raise ApiFail(409, "busy",
                                  "Wait for the current transcription "
                                  "to finish.")
                if not path or not Path(path).exists():
                    raise ApiFail(400, "input_not_found",
                                  f"No such file:\n{path}", path=path)
                info = open_transcript_info(path)
                session = backend._open_review(info)
            except ApiFail as e:
                _fail(e)
            backend.controller.last_output = path
            _recent_add(path)
            backend.broker.publish("recents", {})
            backend.controller._append_log(
                f"\nLoaded transcript from: {path}\n")
            return {"review": session.payload()}

        @app.post("/api/review/<action>")
        def api_review_mutate(action):
            if action in ("save", "close"):
                return _review_lifecycle(action)
            body = bottle.request.json or {}
            try:
                session = backend._live_review()
                return session.mutate(int(body.get("rev", -1)),
                                      action, body)
            except ApiFail as e:
                _fail(e)

        def _review_lifecycle(action):
            body = bottle.request.json or {}
            try:
                session = backend._live_review()
                if action == "save":
                    out_path = session.save(
                        int(body.get("rev", -1)),
                        str(body.get("mode", "labels")),
                        extra_queue=backend.controller.queue)
                    backend.controller.last_output = out_path
                    backend.review = None
                    return {"out_path": out_path}
                session.close_discard()
                backend.review = None
                return {"ok": True}
            except ApiFail as e:
                _fail(e)

        @app.get("/api/autosave")
        def api_autosave():
            data = _autosave_load()
            if not data:
                return {"pending": False}
            name = Path(data.get("out_path") or "").name or "transcript"
            return {"pending": True, "name": name,
                    "saved_at": data.get("saved_at")}

        @app.post("/api/autosave/restore")
        def api_autosave_restore():
            data = _autosave_load()
            try:
                if not data:
                    raise ApiFail(404, "no_autosave",
                                  "Nothing to restore.")
                if backend.review is not None and not backend.review.closed:
                    raise ApiFail(409, "review_open",
                                  "A review session is already open.")
                session = backend._open_review(autosave_restore_info(data))
            except ApiFail as e:
                _fail(e)
            backend.controller._append_log(
                f"Restored unsaved review session: {session.out_path}\n")
            return {"review": session.payload()}

        @app.post("/api/autosave/discard")
        def api_autosave_discard():
            _autosave_clear()
            return {"ok": True}

        # -- audio ---------------------------------------------------------

        @app.get("/audio/current")
        def audio_current():
            prep = backend.audio
            if (prep is None or prep.state != "ready"
                    or not prep.serve_path):
                _fail(ApiFail(404, "no_audio", "No audio is ready."))
            p = Path(prep.serve_path)
            mimetype = ("audio/mp4" if p.suffix.lower() == ".m4a"
                        else "auto")
            return bottle.static_file(p.name, root=str(p.parent),
                                      mimetype=mimetype)

        # -- run / batch -------------------------------------------------

        @app.post("/api/run")
        def api_run():
            body = bottle.request.json or {}
            try:
                if backend.review is not None and not backend.review.closed:
                    raise ApiFail(409, "review_open",
                                  "Finish the open review first.")
                run_id = backend.controller.start_single(
                    body.get("input"), body.get("output"),
                    current_settings(), force=bool(body.get("force")))
            except ApiFail as e:
                _fail(e)
            return {"run_id": run_id}

        @app.post("/api/batch")
        def api_batch():
            body = bottle.request.json or {}
            try:
                if backend.review is not None and not backend.review.closed:
                    raise ApiFail(409, "review_open",
                                  "Finish the open review first.")
                run_id = backend.controller.start_batch(
                    body.get("files") or [], current_settings(),
                    force=bool(body.get("force")))
            except ApiFail as e:
                _fail(e)
            return {"run_id": run_id,
                    "count": len(body.get("files") or [])}

        @app.post("/api/run/stop")
        def api_run_stop():
            stopping = backend.controller.stop()
            return {"ok": True, "stopping": stopping}

        # -- files & dialogs ----------------------------------------------

        @app.post("/api/files/inspect")
        def api_files_inspect():
            body = bottle.request.json or {}
            fmt = current_settings()["output_format"]
            out = []
            for p in (body.get("paths") or [])[:200]:
                try:
                    exists = Path(p).exists()
                except OSError:
                    exists = False
                out.append({
                    "path": p,
                    "name": Path(p).name,
                    "exists": exists,
                    "derived_output": str(Path(p).with_suffix(
                        f".transcript.{fmt}")),
                })
            return {"files": out}

        @app.post("/api/pick")
        def api_pick():
            body = bottle.request.json or {}
            try:
                result = backend.pick_files(
                    kind=body.get("kind", "media"),
                    multiple=bool(body.get("multiple")),
                    initial=body.get("initial"),
                    fmt=body.get("format"))
            except ApiFail as e:
                _fail(e)
            return result

        # -- paths, log, readme -------------------------------------------

        @app.post("/api/path/open")
        def api_path_open():
            body = bottle.request.json or {}
            p = body.get("path") or ""
            if not Path(p).exists():
                _fail(ApiFail(400, "not_found", f"No such file: {p}"))
            _open_path(p)
            return {"ok": True}

        @app.post("/api/path/reveal")
        def api_path_reveal():
            body = bottle.request.json or {}
            p = body.get("path") or ""
            if not Path(p).exists():
                _fail(ApiFail(400, "not_found", f"No such file: {p}"))
            _reveal_path(p)
            return {"ok": True}

        @app.get("/api/log")
        def api_log():
            bottle.response.content_type = "text/plain; charset=utf-8"
            try:
                lines = int(bottle.request.query.get("lines", "500"))
            except ValueError:
                lines = 500
            try:
                text = _log_file_path().read_text(errors="replace")
            except OSError:
                return ""
            return "\n".join(text.splitlines()[-lines:])

        @app.post("/api/log/open")
        def api_log_open():
            body = bottle.request.json or {}
            p = _log_file_path()
            if not p.exists():
                _fail(ApiFail(400, "not_found", "No log file yet."))
            if body.get("reveal"):
                _reveal_path(str(p))
            else:
                _open_path(str(p))
            return {"ok": True}

        @app.get("/api/readme")
        def api_readme():
            p = _find_readme()
            if p is None:
                _fail(ApiFail(404, "not_found", "README not found."))
            try:
                return {"path": str(p), "text": p.read_text(
                    encoding="utf-8", errors="replace")}
            except OSError as e:
                _fail(ApiFail(500, "read_failed", str(e)))

        @app.post("/api/client-error")
        def api_client_error():
            body = bottle.request.json or {}
            _log("web client error: "
                 f"{body.get('message')}\n{body.get('stack', '')}")
            return {"ok": True}

        # -- recents -------------------------------------------------------

        @app.get("/api/recents")
        def api_recents():
            return {"items": _recents_payload()}

        @app.post("/api/recents/clear")
        def api_recents_clear():
            _recent_save([])
            backend.broker.publish("recents", {})
            return {"ok": True}

        # -- settings ----------------------------------------------------

        @app.get("/api/settings")
        def api_settings_get():
            return current_settings()

        @app.put("/api/settings")
        def api_settings_put():
            try:
                incoming = bottle.request.json
            except Exception:
                incoming = None
            if not isinstance(incoming, dict):
                raise bottle.HTTPResponse(
                    body=json.dumps({"error": {
                        "code": "bad_request",
                        "message": "Body must be a JSON object."}}),
                    status=400,
                    headers={"Content-Type": "application/json"})
            merged = validate_settings(incoming, base=current_settings())
            _settings_save(merged)
            return merged

        # -- events ------------------------------------------------------

        @app.get("/api/events")
        def api_events():
            bottle.response.content_type = "text/event-stream"
            bottle.response.set_header("Cache-Control", "no-cache")
            raw_last = bottle.request.get_header("Last-Event-ID")
            try:
                last_id = int(raw_last) if raw_last else None
            except ValueError:
                last_id = None
            q, backlog = backend.broker.subscribe(last_id)

            def stream():
                try:
                    # Padding defeats intermediary buffering; retry tells
                    # EventSource how quickly to return after a drop.
                    yield ":" + (" " * 2048) + "\n"
                    yield "retry: 2000\n\n"
                    if backlog is None:
                        yield "event: resync\ndata: {}\n\n"
                    else:
                        for item in backlog:
                            yield _sse_format(item)
                    while True:
                        try:
                            item = q.get(timeout=15.0)
                        except queue.Empty:
                            yield ": ping\n\n"
                            continue
                        yield _sse_format(item)
                finally:
                    backend.broker.unsubscribe(q)

            return stream()

        # -- lifecycle ---------------------------------------------------

        @app.post("/api/shutdown")
        def api_shutdown():
            if backend.server is not None:
                threading.Thread(target=backend.server.shutdown,
                                 daemon=True).start()
            return {"ok": True}

        return app

    # -- native dialogs ----------------------------------------------------

    def pick_files(self, *, kind, multiple=False, initial=None, fmt=None):
        """Show a native file dialog and return real filesystem paths.
        With a pywebview window this is the OS dialog (callable from
        any thread; blocks until dismissed); in --serve browser mode it
        falls back to a one-shot tkinter dialog in a subprocess -
        development only."""
        if self.window is not None:
            return self._pick_files_native(kind=kind, multiple=multiple,
                                           initial=initial, fmt=fmt)
        script = _PICK_SCRIPT
        args = [sys.executable, "-c", script, kind,
                "1" if multiple else "0", initial or "", fmt or ""]
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ApiFail(501, "no_dialog",
                          f"No file dialog available: {e}")
        if proc.returncode != 0:
            raise ApiFail(501, "no_dialog",
                          "No file dialog available in this mode "
                          "(tkinter missing). Type the path instead.")
        import json
        try:
            paths = json.loads(proc.stdout.strip() or "[]")
        except ValueError:
            paths = []
        if not paths:
            return {"cancelled": True}
        if multiple:
            return {"paths": paths}
        return {"path": paths[0]}

    def _pick_files_native(self, *, kind, multiple, initial, fmt):
        import webview
        try:
            dialog_open = webview.FileDialog.OPEN
            dialog_save = webview.FileDialog.SAVE
        except AttributeError:      # older pywebview constants
            dialog_open = webview.OPEN_DIALOG
            dialog_save = webview.SAVE_DIALOG

        if kind == "save-output":
            ext = fmt or "docx"
            result = self.window.create_file_dialog(
                dialog_save,
                save_filename=initial or f"transcript.{ext}")
        else:
            if kind == "transcript":
                types = ("Transcripts (*.docx;*.txt)", "All files (*.*)")
            else:
                types = ("Audio and video (*.mp3;*.wav;*.m4a;*.aac;"
                         "*.flac;*.ogg;*.opus;*.mp4;*.mov;*.mkv;*.avi;"
                         "*.webm)", "All files (*.*)")
            result = self.window.create_file_dialog(
                dialog_open, allow_multiple=multiple, file_types=types)

        if not result:
            return {"cancelled": True}
        paths = ([result] if isinstance(result, str) else
                 [str(p) for p in result])
        if multiple:
            return {"paths": paths}
        return {"path": paths[0]}

    # -- plumbing ---------------------------------------------------------

    def serve(self, host="127.0.0.1", port=0):
        """Bind the server (port 0 = OS-assigned) and return it; the
        caller decides which thread runs serve_forever()."""
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import (WSGIServer, WSGIRequestHandler,
                                           make_server)

        class _Server(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        class _Handler(WSGIRequestHandler):
            def log_message(self, format, *args):
                pass    # no stdout/stderr chatter (pythonw on Windows)

        self.server = make_server(host, port, self.build_app(),
                                  server_class=_Server,
                                  handler_class=_Handler)
        return self.server


# One-shot native file dialog for --serve (browser) mode, executed as
# `python -c` in a subprocess so no Tk root lingers in the server
# process. argv: kind multiple initial format; prints a JSON list.
_PICK_SCRIPT = r"""
import json, sys
import tkinter as tk
from tkinter import filedialog

kind, multiple, initial, fmt = sys.argv[1:5]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)

MEDIA = [("Audio/video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus "
                         "*.mp4 *.mov *.mkv *.avi *.webm"),
         ("All files", "*.*")]
TRANSCRIPT = [("Transcripts", "*.docx *.txt"), ("All files", "*.*")]

paths = []
if kind == "save-output":
    ext = fmt or "docx"
    p = filedialog.asksaveasfilename(
        defaultextension=f".{ext}",
        initialfile=initial or "",
        filetypes=[(ext, f"*.{ext}"), ("All files", "*.*")])
    if p:
        paths = [p]
else:
    types = TRANSCRIPT if kind == "transcript" else MEDIA
    if multiple == "1":
        got = filedialog.askopenfilenames(filetypes=types)
        paths = list(got or [])
    else:
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            paths = [p]
root.destroy()
print(json.dumps(paths))
"""


# =====================================================================
# Entry point
# =====================================================================

def _parse_args(argv):
    import argparse
    p = argparse.ArgumentParser(
        prog="transcribr",
        description="Transcribr - local Whisper transcription GUI.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--web", dest="ui", action="store_const", const="web",
                   help="use the web-based interface (native window)")
    g.add_argument("--tk", dest="ui", action="store_const", const="tk",
                   help="use the classic Tk interface")
    p.add_argument("--serve", action="store_true",
                   help="run the web backend only and print its URL "
                        "(development, or using the app from a browser)")
    p.add_argument("--port", type=int, default=None,
                   help="port for --serve (default 8737)")
    p.add_argument("--dev-token", default=None,
                   help="fixed API token for --serve (default 'dev')")
    p.set_defaults(ui=None)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.serve:
        return main_serve(args)
    ui = args.ui or os.environ.get("TRANSCRIBR_UI", "tk").strip().lower()
    if ui == "web":
        return main_web(args)
    return main_tk()


def _require_web_stack():
    """Exit with instructions rather than a traceback when the web
    interface is requested under a Python that lacks bottle (the
    installers add it to Transcribr's own environment; a bare system
    python usually won't have it)."""
    try:
        import bottle  # noqa: F401
        return
    except ImportError:
        pass
    if sys.platform == "darwin":
        venv_python = (Path.home() / "Library" / "Application Support"
                       / "Transcribr" / "venv" / "bin" / "python")
    elif sys.platform == "win32":
        venv_python = (Path(os.environ.get("LOCALAPPDATA", ""))
                       / "Transcribr" / "venv" / "Scripts" / "python.exe")
    else:
        venv_python = None
    lines = [
        "The web interface needs the 'bottle' package, which is not",
        f"installed for this Python ({sys.executable}).",
        "",
    ]
    if venv_python is not None and venv_python.exists():
        lines += [
            "Easiest fix - run Transcribr through its installed "
            "environment:",
            f'  "{venv_python}" {Path(__file__).name} --serve',
            "",
        ]
    lines += [
        "Or install bottle for this Python:",
        "  python3 -m pip install bottle",
    ]
    sys.exit("\n".join(lines))


def main_serve(args):
    """Web backend without a window: for development and as the
    escape hatch when the native window can't start."""
    _require_web_stack()
    token = args.dev_token or "dev"
    backend = WebBackend(token)
    backend.start_pump()
    server = backend.serve(port=args.port if args.port is not None else 8737)
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    _log(f"--serve listening on {server.server_port}")
    print(f"Transcribr {__version__} web backend running:\n  {url}")
    print("Open the URL in a browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _attach_drop_handler(backend, window):
    """Bridge OS drag-and-drop to the front end. pywebview reveals the
    dropped files' real filesystem paths on the PYTHON side only
    (pywebviewFullPath); we forward them as a files_dropped SSE event
    and the page applies the 1-file/many-files rule. prevent_default on
    dragover is required or the drop never fires."""
    try:
        from webview.dom import DOMEventHandler

        def on_drop(e):
            files = (e.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files
                     if isinstance(f, dict)]
            paths = [p for p in paths if p]
            if paths:
                backend.broker.publish("files_dropped", {"paths": paths})

        window.dom.document.events.dragover += DOMEventHandler(
            lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(
            on_drop, prevent_default=True)
    except Exception:
        _log("drop-handler attach failed:\n" + traceback.format_exc())


def main_web(args):
    """The desktop app: the web backend on daemon threads plus a
    native window (WKWebView / WebView2) pointed at it. webview.start()
    must own the main thread - notably on macOS."""
    _require_web_stack()
    try:
        import webview  # noqa: F401
    except ImportError:
        sys.exit(
            "The native window needs the 'pywebview' package, which is "
            f"not installed for this Python ({sys.executable}).\n"
            "Install it with:\n"
            "  python3 -m pip install pywebview\n"
            "or run without a window:  python3 transcribr.py --serve")
    import webview

    # Menu-bar/taskbar identity (ports of the Tk main()'s NSBundle
    # rename; AppUserModelID keeps the Windows taskbar icon grouped).
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = (bundle.localizedInfoDictionary()
                        or bundle.infoDictionary())
                if info is not None:
                    info["CFBundleName"] = "Transcribr"
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "local.transcribr")
        except Exception:
            pass

    if args.dev_token:
        token = args.dev_token
    else:
        import secrets
        token = secrets.token_urlsafe(32)

    backend = WebBackend(token)
    backend.has_window = True
    backend.start_pump()
    server = backend.serve(port=args.port if args.port is not None else 0)
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="transcribr-http").start()
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    _log(f"Transcribr {__version__} web window starting - "
         f"port {server.server_port}")

    window = webview.create_window(
        f"Transcribr {__version__}", url,
        width=1200, height=800, min_size=(900, 620))
    backend.window = window

    def on_loaded():
        _attach_drop_handler(backend, window)

    def on_closed():
        # A pending (debounced) autosave must not be lost with the
        # window; flush it, then stop the server.
        try:
            if backend.review is not None and not backend.review.closed:
                backend.review.model.flush_autosave()
        except Exception:
            _log("autosave flush on close failed:\n"
                 + traceback.format_exc())
        finally:
            threading.Thread(target=server.shutdown,
                             daemon=True).start()

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()


def main_tk():
    # On macOS the menu bar titles itself after the running process, which
    # for a plain interpreter launch is "Python". Rewriting the bundle's
    # name before Tk starts the application makes the menu bar (and the
    # app menu) read "Transcribr". Needs pyobjc; silently skipped without.
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = (bundle.localizedInfoDictionary()
                        or bundle.infoDictionary())
                if info is not None:
                    info["CFBundleName"] = "Transcribr"
        except Exception:
            pass

    # Try the DnD-aware root first if tkinterdnd2 is installed. The package
    # itself can import fine but fail at Tk() time on systems where the
    # bundled tkdnd binary isn't compatible (notably Homebrew Python 3.13 on
    # Apple Silicon at the time of writing). In that case we fall back to a
    # standard Tk root - the GUI runs normally, just without drag-and-drop.
    global DND_AVAILABLE

    root = None
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except (tk.TclError, RuntimeError) as e:
            print(
                f"Note: drag-and-drop is unavailable ({e}).\n"
                "      Falling back to standard mode. To enable DnD on "
                "Apple Silicon you can try:\n"
                "        pip uninstall tkinterdnd2\n"
                "        pip install tkinterdnd2-universal",
                file=sys.stderr,
            )
            DND_AVAILABLE = False

    if root is None:
        try:
            root = tk.Tk()
        except tk.TclError as e:
            sys.exit(
                f"Could not start the GUI: {e}\n\n"
                "On macOS with Homebrew Python, Tkinter is a separate "
                "package.\n"
                "Install it with:\n"
                "  brew install python-tk@3.12   "
                "(match your Python version)\n"
            )
    _install_crash_logging(root)
    _log(
        f"Transcribr {__version__} starting — "
        f"python={sys.version.split()[0]} platform={sys.platform} "
        f"dnd={'on' if DND_AVAILABLE else 'off'}"
    )
    _set_window_icon(root)
    WhisperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
