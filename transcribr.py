#!/usr/bin/env python3
"""
Transcribr - GUI for transcribing audio/video files with Whisper
and grouping the result into paragraphs ready for speaker assignment.

(c) James Leaver, 2026. This software is experimental. It relies on
OpenAI's Whisper to transcribe audio and then a separate script to parse
the text into likely paragraphs. It will output a .txt file. It does
those things locally on your computer. When a particular model is run
for the first time, that model will be downloaded to your computer and
stored locally. The 'medium.en' or 'large-v3-turbo' models are
recommended. Use at your own risk. Questions: jleaver@sgchambers.com.au.

Run with:
    python3 transcribr.py
"""

ABOUT_TEXT = (
    "(c) James Leaver, 2026.\n\n"
    "This software is experimental. It relies on OpenAI's Whisper to "
    "transcribe audio and then a separate script to parse the text into "
    "likely paragraphs. It will output a .txt file. It does those things "
    "locally on your computer.\n\n"
    "When a particular model is run for the first time, that model will "
    "be downloaded to your computer and stored locally. The 'medium.en' "
    "or 'large-v3-turbo' models are recommended.\n\n"
    "Use at your own risk.\n\n"
    "Questions: jleaver@sgchambers.com.au"
)

import contextlib
import os
import queue
import re
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
    if curr_start - prev_end >= gap_threshold:
        return True
    if prev_text.rstrip().endswith("?"):
        return True
    if _is_short_response(prev_text) or _is_short_response(curr_text):
        return True
    return False


def paragraphify(segments, gap_threshold: float):
    paragraphs, current, prev = [], [], None
    for seg in segments:
        if current and _should_break(prev, seg, gap_threshold):
            paragraphs.append(current)
            current = []
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
        # Build the paragraph block. Speaker line (if any), then content.
        block_lines = []
        if speaker and speaker != last_speaker:
            block_lines.append(f"{speaker}:")
        if show_timestamp:
            block_lines.append(f"{format_timestamp(start)}  {body}")
        else:
            block_lines.append(body)
        out_lines.append("\n".join(block_lines))
        if speaker:
            last_speaker = speaker

    return "\n\n".join(out_lines) + "\n"


def write_paragraphs_to_file(paragraphs, out_path, *, show_timestamp=True,
                              title=None, output_format="txt", speakers=None):
    """Single entry point for writing transcript output.

    Centralises the txt-vs-docx switch so the worker (direct-write path)
    and the GUI (review-screen path) can both call the same function.

    Raises ImportError with a friendly message if .docx is requested but
    python-docx isn't installed.
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
        return _parse_txt_transcript(path)
    if suffix == ".docx":
        return _parse_docx_transcript(path)
    raise TranscriptParseError(
        f"Unsupported file extension: {suffix}. Only .txt and .docx are supported."
    )


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
            current_speaker = lines[0].rstrip().rstrip(":").strip()
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
            current_speaker = text.rstrip(":").strip()
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


def transcribe_worker(params, q, cancel_event):
    """Run in a background thread; report progress and results via the queue.

    Cancellation is implemented by monkey-patching tqdm.update during the
    transcribe call. tqdm.update is invoked once per chunk-loop iteration
    inside whisper.transcribe(), giving us a regular check-in point. When
    cancel_event is set, the patched update raises _CancelledByUser, which
    we catch outside whisper's call site and use to recover partial segments.

    Whisper accumulates segments in a list referenced by a closure inside
    transcribe(); we can't reach that list directly. Instead, we wrap
    `verbose=True`-style progress so each completed segment is captured by
    a callback before whisper appends it internally.
    """
    captured_segments = []
    used_partial = False

    try:
        q.put(("log", "Importing whisper...\n"))
        try:
            import whisper
            import tqdm
        except ImportError:
            q.put(("error",
                "The 'openai-whisper' package is not installed in this Python.\n\n"
                "Install it with:\n  pip install openai-whisper\n\n"
                "If you are using a virtual environment, make sure this app is "
                "launched from within that environment."))
            return

        q.put(("log", f"Loading model '{params['model']}'...\n"))
        t0 = time.time()
        model = whisper.load_model(params["model"])
        q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))

        if cancel_event.is_set():
            q.put(("cancelled", None))
            return

        q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
        t0 = time.time()

        # Build kwargs only for options whisper expects.
        kwargs = dict(
            language=params["language"],            # may be None for auto-detect
            task=params["task"],
            temperature=params["temperature"],
            compression_ratio_threshold=params["compression_ratio_threshold"],
            logprob_threshold=params["logprob_threshold"],
            no_speech_threshold=params["no_speech_threshold"],
            condition_on_previous_text=params["condition_on_previous_text"],
            word_timestamps=params["word_timestamps"],
            verbose=True,                            # stream segment-by-segment
        )
        if params.get("beam_size") and params["beam_size"] > 1:
            kwargs["beam_size"] = params["beam_size"]
        if params.get("best_of"):
            kwargs["best_of"] = params["best_of"]
        if params.get("initial_prompt"):
            kwargs["initial_prompt"] = params["initial_prompt"]

        # ---- Capture segments as whisper prints them ----
        # With verbose=True, whisper writes one line per finished segment to
        # stdout in the form "[00:00.000 --> 00:05.000]  text". We parse those
        # lines so that if the user cancels, we still have everything that
        # had been transcribed up to the cancellation point.
        ts_re = re.compile(
            r"\[(\d+):(\d+(?:\.\d+)?)\s*-->\s*(\d+):(\d+(?:\.\d+)?)\]\s*(.*)"
        )

        class _CapturingWriter:
            def __init__(self, q_, audio_duration_, t0_):
                self.q = q_
                self._buf = ""
                self.audio_duration = audio_duration_  # may be None
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
                            # Push an ETA update. Only meaningful once we
                            # know the audio duration AND we have a few
                            # seconds of audio processed (otherwise the
                            # speed estimate is wildly noisy).
                            if self.audio_duration and end >= 5.0:
                                wall = time.time() - self.transcribe_start
                                if wall > 0:
                                    speed = end / wall  # audio-s / wall-s
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

        audio_duration = params.get("audio_duration")  # may be None
        writer = _CapturingWriter(q, audio_duration, t0)

        # ---- Patch tqdm.update to honour cancel_event ----
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

        # If the user wants the review screen, hand the paragraphs back
        # to the GUI and stop here. The GUI will eventually call
        # write_paragraphs_to_file() once the user finishes labelling.
        # We pass everything the writer will need so the GUI doesn't
        # have to reconstruct it.
        if params.get("review_before_save"):
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
                },
            ))
            return

        # Direct-write path (unchanged behaviour from before review mode).
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

        # Optionally write Whisper's other output formats. Only available on
        # full (non-cancelled) runs because we need the complete result dict.
        extra_formats = params.get("extra_formats") or []
        if extra_formats:
            if used_partial:
                q.put(("log",
                       f"\nSkipping additional outputs ({', '.join(extra_formats)}) - "
                       "they require a full transcription run.\n"))
            else:
                _write_extra_formats(result, out_path, extra_formats, q)

        q.put(("done", str(out_path)))

    except Exception as e:
        q.put(("error",
               f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"))


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

    doc = Document()

    # Slightly tighter margins to leave more room for the indented body.
    for section in doc.sections:
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

    last_speaker = None
    for i, para in enumerate(paragraphs):
        start = para[0][0]
        body = " ".join(seg[2] for seg in para).lstrip()
        speaker = speakers[i] if speakers and i < len(speakers) else None

        # Emit a speaker label only when it changes. If the user assigned
        # the same speaker to four consecutive paragraphs, the label
        # appears once above the first.
        if speaker and speaker != last_speaker:
            doc.add_paragraph(speaker, style="SpeakerLabel")
        if speaker:
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

    # Footer: "Page X of Y" right-aligned. python-docx doesn't expose Word
    # field codes directly so we drop in the raw XML.
    def _add_field(paragraph, instr):
        run = paragraph.add_run()
        f1 = OxmlElement("w:fldChar"); f1.set(qn("w:fldCharType"), "begin")
        it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve")
        it.text = instr
        f2 = OxmlElement("w:fldChar"); f2.set(qn("w:fldCharType"), "end")
        run._r.append(f1); run._r.append(it); run._r.append(f2)

    fp = doc.sections[0].footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fp.add_run("Page ")
    _add_field(fp, " PAGE ")
    fp.add_run(" of ")
    _add_field(fp, " NUMPAGES ")

    doc.save(str(out_path))


def _write_extra_formats(result, txt_out_path, formats, q):
    """Write Whisper's standard output formats next to the paragraph .txt."""
    try:
        from whisper.utils import get_writer
    except ImportError:
        q.put(("log", "Note: whisper.utils not importable; skipping extra outputs.\n"))
        return

    output_dir = txt_out_path.parent
    # The writer uses the basename of an audio path to decide the output
    # filename. We give it a synthetic path whose stem matches the .txt's
    # stem (without the trailing ".transcript" qualifier, if present), so
    # files end up nicely paired alongside the .txt.
    stem = txt_out_path.stem
    for suffix in (".transcript", ".paragraphs"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    fake_audio = output_dir / (stem + ".audio")

    writer_options = {
        "max_line_width": None,
        "max_line_count": None,
        "highlight_words": False,
    }

    for fmt in formats:
        try:
            writer = get_writer(fmt, str(output_dir))
            try:
                writer(result, str(fake_audio), writer_options)
            except TypeError:
                # Older Whisper versions take only (result, audio_path).
                writer(result, str(fake_audio))
            q.put(("log", f"Wrote: {stem}.{fmt}\n"))
        except Exception as e:
            q.put(("log",
                   f"WARNING: failed to write .{fmt}: {type(e).__name__}: {e}\n"))


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

    SPEAKER_LETTERS = ["1", "2", "3", "4"]
    DEFAULT_NAMES = {
        "1": "Speaker 1",
        "2": "Speaker 2",
        "3": "Speaker 3",
        "4": "Speaker 4",
    }
    # Background colours for each speaker. Picked to be subtle so the
    # text remains the visual focus.
    SPEAKER_COLOURS = {
        "1": "#fff4d6",   # warm yellow
        "2": "#dfeeff",   # soft blue
        "3": "#e3f4d8",   # soft green
        "4": "#f4d8e6",   # soft pink
    }

    def __init__(self, parent, paragraphs, *, on_save, on_cancel,
                 show_timestamp=True):
        super().__init__(parent)
        self.paragraphs = list(paragraphs)
        # Speaker letter (A/B/C/D) per paragraph, or None.
        self.speakers = [None] * len(self.paragraphs)
        # Speaker letter -> human name. Initially "Speaker A" etc.
        self.speaker_names = dict(self.DEFAULT_NAMES)
        self.show_timestamp = show_timestamp
        self.on_save_cb = on_save
        self.on_cancel_cb = on_cancel
        self.selected_idx = 0 if self.paragraphs else None
        self.row_widgets = []  # parallel list of dicts
        # When edit mode is active for a paragraph, this is its index;
        # otherwise None. Only one row can be in edit mode at a time.
        self.editing_idx = None
        # When entering edit mode, we snapshot the body text so Esc can
        # restore it. Cleared when edit mode exits.
        self._edit_original_text = None

        self._build_ui()
        self._refresh_all_rows()
        if self.selected_idx is not None:
            self._select_row(self.selected_idx)

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
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.rows_container = tk.Frame(self.canvas, background="white")
        self.rows_window = self.canvas.create_window(
            (0, 0), window=self.rows_container, anchor="nw")

        # Resize the inner frame to match the canvas width so rows fill horizontally.
        def _on_canvas_configure(event):
            self.canvas.itemconfigure(self.rows_window, width=event.width)
        self.canvas.bind("<Configure>", _on_canvas_configure)

        def _on_frame_configure(event):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.rows_container.bind("<Configure>", _on_frame_configure)

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

        # Bind on the canvas and on the rows_container; we'll add the
        # forwarding binding to each row's body Text below as it's built.
        self.canvas.bind("<MouseWheel>", _on_mousewheel)
        self.canvas.bind("<Button-4>", _on_mousewheel)
        self.canvas.bind("<Button-5>", _on_mousewheel)
        self.rows_container.bind("<MouseWheel>", _on_mousewheel)
        self.rows_container.bind("<Button-4>", _on_mousewheel)
        self.rows_container.bind("<Button-5>", _on_mousewheel)
        # Stash the handler so per-row body widgets can also bind to it.
        self._on_mousewheel = _on_mousewheel

        # Build paragraph rows.
        for i, para in enumerate(self.paragraphs):
            self._build_row(i, para)

        # Help line
        help_line = ttk.Label(
            self,
            text=(
                "Up/Down navigate  ·  1-4 set speaker  ·  0 clear  ·  "
                "M merge with previous  ·  Double-click a word to split  ·  "
                "Enter to edit text"
            ),
            foreground="gray",
        )
        help_line.pack(fill="x", padx=10, pady=(2, 4))

        # Action buttons
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(4, 10))
        save_btn = ttk.Button(actions, text="Save with labels",
                              command=self._on_save_clicked)
        save_btn.pack(side="left")
        cancel_btn = ttk.Button(actions, text="Save without labels",
                                command=self._on_cancel_clicked)
        cancel_btn.pack(side="right")

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

    def _build_row(self, idx, para):
        """Append a new row at the end of row_widgets (used during initial
        build). Use _build_and_insert_row for insertions in the middle."""
        self._construct_row(idx, para, before_row=None)
        # _construct_row already appended to self.row_widgets[idx].

    def _build_and_insert_row(self, idx, para):
        """Build a new row for paragraphs[idx] and insert it into the
        row_widgets list at position `idx`. The Tk widget is packed
        before the row that's currently at position `idx` (which will
        become idx+1 after we re-index)."""
        # Find the row that currently sits at idx, so we can pack BEFORE it.
        # If idx is past the end, we just append.
        before_row = None
        if idx < len(self.row_widgets):
            before_row = self.row_widgets[idx]["row"]
        self._construct_row(idx, para, before_row=before_row,
                            insert_at=idx)

    def _construct_row(self, idx, para, *, before_row=None, insert_at=None):
        # The row's identity is its dict, NOT its index. We pre-allocate
        # the dict here and have all bindings refer to `r["idx"]` rather
        # than capturing `idx` directly. When merge/split shifts rows
        # later, we just mutate `r["idx"]` and all bindings continue to
        # work.
        r = {"idx": idx}

        row = tk.Frame(self.rows_container, background="white",
                       highlightthickness=1, highlightbackground="white",
                       cursor="hand2")
        # When inserting in the middle of an existing list, pack BEFORE
        # the next existing row so Tk lays them out in the right order.
        # When appending, before_row is None and Tk's default end-of-pack
        # behaviour applies.
        if before_row is not None:
            row.pack(fill="x", padx=2, pady=1, before=before_row)
        else:
            row.pack(fill="x", padx=2, pady=1)
        r["row"] = row

        # Speaker badge: 1/2/3/4 or "-".
        # Explicit foreground because tk.Label inherits the system text
        # colour by default, which on macOS dark mode is light grey -
        # invisible against our explicit white background.
        badge = tk.Label(row, text="-", width=2,
                        font=("TkDefaultFont", 10, "bold"),
                        bg="white", fg="black",
                        relief="solid", borderwidth=1)
        badge.pack(side="left", padx=(4, 8), pady=4, anchor="n")
        r["badge"] = badge

        # Timestamp
        start_ts = para[0][0]
        ts_text = format_timestamp(start_ts) if self.show_timestamp else ""
        ts = tk.Label(row, text=ts_text, foreground="#666",
                     font=("Courier", 9), background="white", anchor="nw")
        ts.pack(side="left", padx=(0, 8), pady=4, anchor="n")
        r["ts"] = ts

        # Body text. We use a tk.Text instead of a Label for two reasons:
        #
        # (1) tk.Text exposes "@x,y wordstart" / "wordend" indices, which
        #     lets us implement double-click-to-split-here.
        # (2) tk.Text wraps cleanly to fit any width, no fixed wraplength.
        #
        # The widget is editable by default. We block writes via the <Key>
        # binding so the user can still position a cursor (handy for the
        # double-click-to-split feature) but can't modify the text.
        body_full = " ".join(seg[2] for seg in para).strip()
        # Ensure a font object exists once for the whole pane.
        if not hasattr(self, "_body_font"):
            import tkinter.font as tkfont
            self._body_font = tkfont.Font(family="TkDefaultFont", size=10)
        body = tk.Text(
            row,
            wrap="word",
            font=self._body_font,
            background="white",
            foreground="black",
            # On macOS dark mode and some Windows themes the default
            # insertbackground (cursor colour) is light - invisible on
            # our explicit white-ish row backgrounds. Pin it to black so
            # the cursor is always clearly visible while editing.
            insertbackground="black",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            height=1,
            padx=0, pady=0,
            spacing1=0, spacing3=0,
        )
        body.insert("1.0", body_full)
        r["body"] = body
        r["body_full"] = body_full

        # Block typing UNLESS this row is in edit mode. The flag check
        # happens at event time using r["idx"], which is updated when
        # rows are reordered.
        def _maybe_block_key(event, _r=r):
            if self.editing_idx == _r["idx"]:
                return None  # let the keypress through
            return "break"
        body.bind("<Key>", _maybe_block_key)

        def _maybe_block_paste(event, _r=r):
            if self.editing_idx == _r["idx"]:
                return None
            return "break"
        body.bind("<<Paste>>", _maybe_block_paste)
        body.bind("<<Cut>>", _maybe_block_paste)

        # Esc cancels the edit, Return commits it (or enters edit mode if
        # we're not already editing). Both fire only when this row's body
        # has keyboard focus, which can happen briefly after a click on
        # the body before our after-scheduled focus-grab runs. Without
        # the not-editing branch, pressing Enter quickly after a click
        # would insert a newline into the body text via Tk's class
        # binding before the toplevel Return binding runs.
        def _on_escape(event, _r=r):
            if self.editing_idx == _r["idx"]:
                self._cancel_edit()
                return "break"
        def _on_return(event, _r=r):
            if self.editing_idx == _r["idx"]:
                self._commit_edit()
            else:
                # Make sure this row is the selected one, then enter edit mode.
                if self.selected_idx != _r["idx"]:
                    self._select_row(_r["idx"])
                self._enter_edit_mode(_r["idx"])
            return "break"  # always swallow the keypress so Tk's class
                            # binding doesn't insert a newline after us
        body.bind("<Escape>", _on_escape)
        body.bind("<Return>", _on_return)

        body.pack(side="left", fill="x", expand=True, pady=4)

        # Click anywhere on the row to select.
        def _click_select(event, _r=r):
            self._select_row(_r["idx"])
        for w in (row, badge, ts, body):
            w.bind("<Button-1>", _click_select)

        # Double-click splits the paragraph at the start of the
        # double-clicked word - UNLESS this row is in edit mode, in
        # which case double-click should do its standard select-a-word
        # thing.
        def _maybe_split_or_select(event, _r=r):
            if self.editing_idx == _r["idx"]:
                return None  # let Tk's default word-select happen
            return self._on_body_double_click(event, _r["idx"])
        body.bind("<Double-Button-1>", _maybe_split_or_select)

        # Auto-size the Text widget to fit its content. Re-read from
        # the widget rather than relying on body_full, so post-edit
        # resizes use the current text.
        #
        # During window resize, Tk fires <Configure> on every Text widget
        # for every pixel of drag. With 100+ rows, that's a flood of
        # work. We debounce by scheduling the resize via after(50ms);
        # if another Configure arrives within that window, the previous
        # scheduled call is cancelled and replaced.
        def _resize_body_now(_r=r):
            b = _r["body"]
            try:
                width_px = b.winfo_width()
                if width_px <= 1:
                    return  # not laid out yet
                current_text = b.get("1.0", "end-1c")
                lines = self._count_wrapped_lines(current_text,
                                                  self._body_font,
                                                  width_px)
                if b.cget("height") != lines:
                    b.configure(height=lines)
            except tk.TclError:
                pass

        def _schedule_resize(event=None, _r=r):
            # Cancel any pending resize for this row.
            pending = _r.get("_resize_after_id")
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except (tk.TclError, ValueError):
                    pass
            _r["_resize_after_id"] = self.after(50, _resize_body_now)

        body.after_idle(_resize_body_now)  # initial sizing, no debounce
        body.bind("<Configure>", _schedule_resize)
        # Stash the resize function so we can call it after edits commit.
        r["_resize"] = _resize_body_now

        # Forward mousewheel events from this row's widgets to the
        # canvas. Without this, scroll events delivered while the
        # pointer is over the body Text get consumed by Text's own
        # built-in MouseWheel handler (which would scroll only that
        # widget's content - which is one line, so it doesn't even do
        # anything visible). Returning "break" stops Text's class
        # binding from firing.
        for w in (row, badge, ts, body):
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", self._on_mousewheel)
            w.bind("<Button-5>", self._on_mousewheel)

        if insert_at is not None:
            self.row_widgets.insert(insert_at, r)
        else:
            self.row_widgets.append(r)

    def _update_row_content(self, idx):
        """Refresh the text and timestamp displayed in row `idx` to match
        the underlying paragraphs[idx] data. Used after merge/split/edit
        instead of destroying and rebuilding the row widgets."""
        if idx < 0 or idx >= len(self.row_widgets):
            return
        rw = self.row_widgets[idx]
        para = self.paragraphs[idx]
        body_full = " ".join(seg[2] for seg in para).strip()
        rw["body_full"] = body_full
        # Replace the body text. Block-typing rule still applies.
        body = rw["body"]
        body.delete("1.0", "end")
        body.insert("1.0", body_full)
        # Update the timestamp label.
        if para:
            start_ts = para[0][0]
            new_ts = format_timestamp(start_ts) if self.show_timestamp else ""
            rw["ts"].config(text=new_ts)
        # Trigger a resize so the body height matches the new content.
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
        # Highlight ring: edit mode (thicker orange) takes priority over
        # the selection ring (thinner blue), which takes priority over
        # the no-ring resting state.
        if idx == self.editing_idx:
            rw["row"].config(highlightbackground="#ff9900",
                             highlightcolor="#ff9900",
                             highlightthickness=3)
        elif idx == self.selected_idx:
            rw["row"].config(highlightbackground="#3a7afe",
                             highlightcolor="#3a7afe",
                             highlightthickness=2)
        else:
            rw["row"].config(highlightbackground=row_bg,
                             highlightcolor=row_bg,
                             highlightthickness=1)

    def _select_row(self, idx):
        # If the user clicks away from a row that's being edited, commit
        # the edits first. This matches spreadsheet behaviour - clicking
        # another cell commits the current cell.
        if self.editing_idx is not None and self.editing_idx != idx:
            self._commit_edit()

        old = self.selected_idx
        self.selected_idx = idx
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
        # Scroll into view
        rw = self.row_widgets[idx]
        self.update_idletasks()
        # Compute the row's position relative to the rows_container
        try:
            y_top = rw["row"].winfo_y()
            y_bot = y_top + rw["row"].winfo_height()
            container_h = self.rows_container.winfo_height()
            if container_h > 0:
                # Adjust the canvas's view so the row is visible.
                view_top, view_bot = self.canvas.yview()
                view_top_px = view_top * container_h
                view_bot_px = view_bot * container_h
                if y_top < view_top_px:
                    self.canvas.yview_moveto(y_top / container_h)
                elif y_bot > view_bot_px:
                    self.canvas.yview_moveto(
                        max(0, (y_bot - (view_bot_px - view_top_px)) / container_h))
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
            original = self._edit_original_text or rw["body_full"]
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

        # Surgical row update: update the existing row in-place, then
        # build a single new row and pack it in the right position.
        # All other rows stay where they are.
        self._update_row_content(idx)
        self._build_and_insert_row(idx + 1, second)
        self._reindex_rows()
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

        # Surgical row update: destroy only the removed row's widgets,
        # update the merged row's content. The remaining ~125 widgets
        # are untouched, which is the whole point of doing this
        # incrementally rather than rebuilding the list.
        removed = self.row_widgets.pop(i)
        try:
            removed["row"].destroy()
        except tk.TclError:
            pass
        # Re-index every row whose position changed (i and onward).
        self._reindex_rows()
        # Refresh the merged row's text/timestamp to reflect the joined
        # paragraph.
        self._update_row_content(i - 1)

        self.selected_idx = i - 1
        # Refresh visual state of the merged row plus the previous selection.
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

    def _on_cancel_clicked(self):
        # Same commit-pending-edit logic for the "Save without labels"
        # path (which is the new behaviour of the cancel button).
        if self.editing_idx is not None:
            self._commit_edit()
        self.on_cancel_cb()

    def destroy(self):
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


class WhisperGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("Transcribr")
        self.root.geometry("900x1020")
        self.root.minsize(700, 800)

        self.queue: "queue.Queue" = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.last_output = None

        self._build_ui()
        self._poll_queue()

    # ----- UI construction ---------------------------------------------------

    def _build_ui(self):
        # Menu bar with File → Open Transcript...
        # On macOS the menu attaches to the system menubar at the top of
        # the screen; on Windows/Linux it attaches to the window itself.
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(
            label="Open Transcript...",
            command=self._load_transcript,
        )
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

        # We keep a reference to the main frame so we can hide it (without
        # destroying it) when entering the review pane after a transcription.
        self.main_frame = ttk.Frame(self.root, padding=12)
        self.main_frame.pack(fill="both", expand=True)
        main = self.main_frame

        self._build_file_section(main)
        self._build_model_section(main)
        self._build_prompt_section(main)
        self._build_paragraph_section(main)
        self._build_extra_outputs_section(main)
        self._build_advanced_section(main)
        self._build_run_section(main)
        self._build_log_section(main)
        self._build_bottom_section(main)

        # Will hold the ReviewPane while it's visible.
        self.review_pane = None
        # Stashed paragraphs_ready info so the save callback knows what to write.
        self._pending_review_info = None

    def _build_file_section(self, parent):
        f = ttk.LabelFrame(parent, text="File", padding=8)
        f.pack(fill="x", pady=(0, 8))

        ttk.Label(f, text="Input:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.input_var = tk.StringVar()
        self.input_var.trace_add("write", self._on_input_changed)
        self.input_entry = ttk.Entry(f, textvariable=self.input_var)
        self.input_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(f, text="Browse...", command=self._pick_input).grid(
            row=0, column=2)

        ttk.Label(f, text="Output:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.output_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(f, text="Browse...", command=self._pick_output).grid(
            row=1, column=2, pady=(6, 0))

        ttk.Label(f, text="Format:").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.output_format_var = tk.StringVar(value="docx")
        fmt_frame = ttk.Frame(f)
        fmt_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Radiobutton(fmt_frame, text=".txt (plain text)",
                        variable=self.output_format_var, value="txt",
                        command=self._on_format_changed).pack(side="left")
        ttk.Radiobutton(fmt_frame, text=".docx (Word)",
                        variable=self.output_format_var, value="docx",
                        command=self._on_format_changed).pack(side="left", padx=(16, 0))

        f.columnconfigure(1, weight=1)

        # Register drop targets if tkinterdnd2 is available. Whole window plus
        # the input Entry, so dropping anywhere works.
        if DND_AVAILABLE:
            for widget in (self.root, self.input_entry, f):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except (AttributeError, tk.TclError):
                    # Not all widget types support dnd registration.
                    pass

    def _on_drop(self, event):
        """Handle a file drop. event.data is a TkDnD-encoded path list."""
        # TkDnD wraps paths with spaces in {curly braces}; tk.splitlist handles
        # that uniformly across platforms.
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            paths = [event.data]
        if paths:
            # Use the first dropped file. Strip stray quotes/braces just in case.
            p = str(paths[0]).strip().strip("{}").strip('"')
            self.input_var.set(p)
        return event.action if hasattr(event, "action") else None

    def _build_model_section(self, parent):
        f = ttk.LabelFrame(parent, text="Model", padding=8)
        f.pack(fill="x", pady=(0, 8))

        ttk.Label(f, text="Model:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.model_var = tk.StringVar(value="large-v3-turbo")
        ttk.Combobox(f, textvariable=self.model_var, values=WHISPER_MODELS,
                     state="readonly", width=14).grid(
            row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(f, text="Language:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.language_var = tk.StringVar(value="English")
        ttk.Combobox(f, textvariable=self.language_var,
                     values=[name for name, _ in LANGUAGES],
                     state="readonly", width=20).grid(
            row=0, column=3, sticky="w", padx=(0, 16))

        ttk.Label(f, text="Task:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.task_var = tk.StringVar(value="transcribe")
        ttk.Combobox(f, textvariable=self.task_var,
                     values=["transcribe", "translate"],
                     state="readonly", width=12).grid(row=0, column=5, sticky="w")

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
        f = ttk.LabelFrame(parent, text="Advanced", padding=8)
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

    def _build_run_section(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(0, 8))
        self.run_btn = ttk.Button(f, text="Run Transcription",
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
        ttk.Label(
            f,
            text="(Pause is not supported by Whisper.)",
            foreground="gray",
        ).pack(side="left", padx=(12, 0))

    def _build_log_section(self, parent):
        # A status row above the log shows ETA / progress while a job is
        # running. It's a single line that changes content; no scrolling.
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill="x", pady=(0, 4))
        self.status_var = tk.StringVar(value="")
        self.status_label = ttk.Label(
            status_frame, textvariable=self.status_var,
            foreground="#444",
        )
        self.status_label.pack(side="left")

        f = ttk.LabelFrame(parent, text="Progress", padding=8)
        f.pack(fill="both", expand=True, pady=(0, 8))
        self.output_text = scrolledtext.ScrolledText(
            f, height=12, wrap="word", state="disabled")
        self.output_text.pack(fill="both", expand=True)

    def _build_bottom_section(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill="x")
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
            if p.suffix.lower() in (".txt", ".docx"):
                self.output_var.set(str(p.with_suffix(new_ext)))

    def _pick_output(self):
        initial = self.output_var.get() or ""
        fmt = self.output_format_var.get()
        ext = "." + fmt
        if fmt == "docx":
            ftypes = [("Word document", "*.docx"), ("All files", "*.*")]
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

    def _on_run(self):
        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()
        if not in_path:
            messagebox.showerror("Missing input",
                                 "Please choose an input audio/video file.")
            return
        if not Path(in_path).exists():
            messagebox.showerror("File not found",
                                 f"Input file does not exist:\n{in_path}")
            return
        if not out_path:
            ext = self.output_format_var.get()
            out_path = str(Path(in_path).with_suffix(f".transcript.{ext}"))
            self.output_var.set(out_path)

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

        params = dict(
            input=in_path,
            output=out_path,
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
            word_timestamps=self.word_ts_var.get(),
            initial_prompt=description or None,
            title=description or None,
            gap=self.gap_var.get(),
            extra_formats=extra_formats,
            output_format=self.output_format_var.get(),
            show_timestamp=self.show_timestamp_var.get(),
            audio_duration=audio_duration,
            review_before_save=self.review_var.get(),
        )

        self.run_btn.config(state="disabled", text="Running...")
        self.stop_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.reveal_btn.config(state="disabled")
        self._clear_log()

        self.cancel_event.clear()
        self.worker = threading.Thread(
            target=transcribe_worker,
            args=(params, self.queue, self.cancel_event),
            daemon=True,
        )
        self.worker.start()

    def _on_stop(self):
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
                    self._enter_review_mode(data)
                elif kind == "done":
                    self._on_done(data)
                elif kind == "error":
                    self._on_error(data)
                elif kind == "cancelled":
                    self._on_cancelled(data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _update_eta(self, info):
        """Update the status line with audio progress + ETA."""
        done = info["audio_done"]
        total = info["audio_total"]
        eta = info["eta_seconds"]
        speed = info["speed"]
        pct = (done / total * 100) if total else 0
        self.status_var.set(
            f"{_format_duration(done)} / {_format_duration(total)} "
            f"({pct:.0f}%)   "
            f"about {_format_duration(eta)} remaining   "
            f"[{speed:.1f}x audio speed]"
        )

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

    def _on_done(self, output_path):
        self.last_output = output_path
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.open_btn.config(state="normal")
        self.reveal_btn.config(state="normal")
        self.status_var.set("Done")
        self._append_log("\n=== Done ===\n")

    def _on_error(self, message):
        self.run_btn.config(state="normal", text="Run Transcription")
        self.stop_btn.config(state="disabled", text="Stop")
        self.status_var.set("")
        self._append_log(f"\n!!! Error !!!\n{message}\n")
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

        # Hide the main UI (don't destroy - we'll bring it back).
        self.main_frame.pack_forget()

        # Show the review pane in its place.
        self.review_pane = ReviewPane(
            self.root,
            info["paragraphs"],
            on_save=self._on_review_save,
            on_cancel=self._on_review_cancel,
            show_timestamp=info.get("show_timestamp", True),
        )
        self.review_pane.pack(fill="both", expand=True)

        # Apply preset speaker assignments if the caller provided them
        # (e.g. when loading an existing transcript with speaker labels).
        preset_speakers = info.get("preset_speakers")
        preset_names = info.get("preset_speaker_names")
        if preset_speakers and preset_names:
            self.review_pane.speakers = list(preset_speakers)
            for letter, name in preset_names.items():
                self.review_pane.speaker_names[letter] = name
                if letter in self.review_pane.name_vars:
                    self.review_pane.name_vars[letter].set(name)
            self.review_pane._refresh_all_rows()

    def _exit_review_mode(self):
        if self.review_pane is not None:
            self.review_pane.destroy()
            self.review_pane = None
        self.main_frame.pack(fill="both", expand=True)

    def _load_transcript(self):
        """Show a file picker, parse the chosen transcript, and enter
        review mode with the parsed content. The output path defaults
        to the same file (so saving overwrites it)."""
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

        # Map full speaker names to numbered slots 1/2/3/4. We need to
        # collapse possibly-many speaker names back into at most 4
        # slots; if there are more we refuse rather than silently
        # losing distinctions.
        unique_names = []
        for name in parsed["speakers"]:
            if name and name not in unique_names:
                unique_names.append(name)
        if len(unique_names) > 4:
            messagebox.showerror(
                "Too many speakers",
                f"This transcript contains {len(unique_names)} distinct "
                "speakers. The review pane only supports up to 4. "
                "Open the file in Word and consolidate the speaker labels "
                "before loading it back into Transcribr.",
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
        }
        self.last_output = path  # so 'Open Output' / 'Show in Folder' work
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
            messagebox.showerror("Cannot write .docx", str(e))
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

    def _on_review_cancel(self):
        """User cancelled review. Save WITHOUT speaker labels so they don't
        lose any work, then exit review mode."""
        info = self._pending_review_info
        if not info:
            self._exit_review_mode()
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
            messagebox.showerror("Cannot write .docx", str(e))
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
# Entry point
# =====================================================================

def main():
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
    _set_window_icon(root)
    WhisperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
