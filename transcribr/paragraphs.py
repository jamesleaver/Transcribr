"""Turning engine segments into paragraphs, and rendering them as text."""

import re
import sys
import traceback

from .speakers import UNATTRIBUTED_LABEL
from .platformutil import _log


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


# ---- Audio decoding (PyAV first, ffmpeg binary as fallback) ----------
#
# PyAV ships FFmpeg's libraries inside its wheel (it is already a hard
# dependency of faster-whisper), so with it installed Transcribr needs
# no ffmpeg/ffprobe binaries at all: duration probing, decoding for the
# engines and the speaker detector, and playback extraction all go
# through it. The subprocess fallbacks remain for environments that
# only have the binaries (e.g. an openai-whisper-only install).

def _have_pyav():
    import importlib.util
    return importlib.util.find_spec("av") is not None


AUDIO_SAMPLE_RATE = 16000


def decode_audio_16k(path):
    """Decode any audio/video file to mono float32 at 16 kHz via PyAV -
    the input format Whisper and the speaker detector both expect.
    Returns a numpy array, or None when PyAV is unavailable or decoding
    fails (callers then fall back to handing the engine the file
    path)."""
    try:
        import av
        import numpy as np
    except ImportError:
        return None
    try:
        frames = []
        with av.open(str(path)) as container:
            if not container.streams.audio:
                return None
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(
                format="s16", layout="mono", rate=AUDIO_SAMPLE_RATE)
            for frame in container.decode(stream):
                for rf in resampler.resample(frame):
                    frames.append(rf.to_ndarray())
            for rf in resampler.resample(None):     # flush
                frames.append(rf.to_ndarray())
        if not frames:
            return None
        pcm = np.concatenate([f.reshape(-1) for f in frames])
        return pcm.astype(np.float32) / 32768.0
    except Exception:
        _log(f"PyAV decode failed for {path}:\n{traceback.format_exc()}")
        return None


# File signatures that regularly turn up wearing an audio extension
# (exports from evidence/records systems are the usual culprits). Used
# to explain a decode failure in plain language instead of dumping an
# ffmpeg traceback on the user.
_NON_AUDIO_SIGNATURES = (
    (b"PK\x03\x04", "a zip archive or Microsoft Office document"),
    (b"%PDF", "a PDF document"),
    (b"{\\rtf", "an RTF document"),
    (b"<!DO", "a web page"),
    (b"<htm", "a web page"),
    (b"\xd0\xcf\x11\xe0", "a legacy Microsoft Office document"),
)


def describe_non_audio_file(path):
    """Why `path` can't be decoded as audio, in plain language, or None
    when it plausibly is audio (only consulted after a decode failed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as e:
        return f"it could not be read ({e.strerror or e})"
    if not head:
        return "it is empty (0 bytes)"
    for magic, kind in _NON_AUDIO_SIGNATURES:
        if head.startswith(magic):
            return (f"it appears to be {kind} that has been given an "
                    "audio file's name, not an actual recording")
    try:
        import av
        with av.open(str(path)) as container:
            if not container.streams.audio:
                return "it contains no audio stream"
        return None
    except ImportError:
        return None
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        return f"it could not be read as audio ({detail})"


def _pyav_duration(path):
    """Duration in seconds via PyAV container metadata, or None."""
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(str(path)) as container:
            # container.duration is in AV_TIME_BASE units (microseconds).
            if container.duration:
                return float(container.duration) / 1_000_000
            for s in container.streams.audio:
                if s.duration and s.time_base:
                    return float(s.duration * s.time_base)
    except Exception:
        return None
    return None


def _ffprobe_duration(path):
    """Duration in seconds via the ffprobe binary, or None.

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


def get_audio_duration(path):
    """Return the duration of an audio/video file in seconds, or None on
    failure. PyAV first; ffprobe when PyAV is missing or stumped."""
    duration = _pyav_duration(path)
    if duration is not None:
        return duration
    return _ffprobe_duration(path)


def _is_short_response(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s'-]", "", text).strip().lower()
    if not cleaned:
        return False
    if cleaned in SHORT_RESPONSES:
        return True
    words = cleaned.split()
    return len(words) <= 3 and all(w in SHORT_RESPONSES for w in words)


# A sentence ending (". !") only breaks a paragraph when the speaker
# also paused: this fraction of the user's gap threshold, floored so a
# very low threshold doesn't turn every full stop into a paragraph.
_SOFT_BREAK_FRACTION = 0.4
_SOFT_BREAK_MIN_SECONDS = 0.35

# Trailing marks Whisper emits when speech is cut off or trails away -
# in dialogue that's a turn change almost every time.
_INTERRUPTION_ENDINGS = ("--", "—", "–", "...", "…")


def _leading_acknowledgment(text: str) -> bool:
    """True when the segment OPENS with a yes/no/okay-style token
    ("Yeah, I did") - a strong cue that a different person is now
    answering, even mid-flow."""
    cleaned = re.sub(r"[^\w\s'-]", " ", text or "").strip().lower()
    first = cleaned.split(" ", 1)[0] if cleaned else ""
    return first in SHORT_RESPONSES


def _gaps_are_informative(segments) -> bool:
    """Whether the segment times carry real silence information.

    faster-whisper without word timestamps emits wall-to-wall segments
    (each end equals the next start), hiding every actual pause. When
    nearly every boundary is contiguous like that, gap-based rules
    would never fire, so the soft cues must stand on their own."""
    if len(segments) < 2:
        return True
    contiguous = sum(1 for a, b in zip(segments, segments[1:])
                     if b[0] - a[1] < 0.05)
    return contiguous / (len(segments) - 1) < 0.7


def _is_sentence_continuation(prev_stripped: str, curr_text: str) -> bool:
    """True when a paragraph break would land mid-sentence: the
    previous segment did not finish a sentence (no terminal
    punctuation) and the next one carries straight on (opens with a
    lowercase letter or a digit - "...the 27th of March" | "2026.").
    Mid-sentence breaks read as errors in a transcript, so this
    overrides even the hard silence rule; the 60-second cap is the
    only thing that can still split such a run."""
    if not prev_stripped or prev_stripped.endswith(
            (".", "!", "?", ":", '"', "”") + _INTERRUPTION_ENDINGS):
        return False
    # A bare short response ("Yes", "Okay") is a complete utterance
    # even when Whisper drops its full stop - never glue onto it.
    if _is_short_response(prev_stripped):
        return False
    first = (curr_text or "").lstrip()[:1]
    return first.islower() or first.isdigit()


def _should_break(prev, curr, gap_threshold: float,
                  gaps_informative: bool = True) -> bool:
    """The paragraph-boundary heuristics, graded by strength:

    - a break never lands mid-sentence (see
      _is_sentence_continuation), whatever the silence;
    - a silence at least the user's threshold breaks;
    - a question mark, an interruption/trail-off ending, or a short
      conversational response always breaks (dialogue turn cues);
    - a sentence ending (". !") or an opening acknowledgment ("Yeah,
      ...") breaks only when paired with a smaller "soft" pause - so
      a monologue read at speed stays one paragraph instead of
      splintering at every full stop, while real turn-taking (which
      nearly always carries a beat of silence) still splits.

    With `gaps_informative` False (the engine recorded no real pauses)
    the soft cues break unconditionally - punctuation is the only
    signal left."""
    if prev is None:
        return True
    _, prev_end, prev_text = prev
    curr_start, _, curr_text = curr
    prev_stripped = prev_text.rstrip()
    gap = curr_start - prev_end
    soft_gap = max(_SOFT_BREAK_MIN_SECONDS,
                   gap_threshold * _SOFT_BREAK_FRACTION)

    # Never split a sentence down the middle.
    if _is_sentence_continuation(prev_stripped, curr_text):
        return False
    # Hard silence beyond the user's pause threshold.
    if gap >= gap_threshold:
        return True
    # A question hands the floor over; the answer is its own paragraph.
    if prev_stripped.endswith("?"):
        return True
    # Cut-off / trailing-away speech is a turn change in dialogue.
    if prev_stripped.endswith(_INTERRUPTION_ENDINGS):
        return True
    # Short conversational responses (yes/no/etc.) on either side.
    if _is_short_response(prev_text) or _is_short_response(curr_text):
        return True
    # Weaker cues need a beat of silence to confirm them - unless no
    # real silence was ever recorded.
    if gap >= soft_gap or not gaps_informative:
        if prev_stripped.endswith((".", "!")):
            return True
        if _leading_acknowledgment(curr_text):
            return True
    return False


# Safety cap: even if no break signal fires, a paragraph shouldn't grow
# beyond this many seconds of audio. Without the cap, an uninterrupted
# monologue can collapse into a single unreadable paragraph in the
# review pane.
_PARAGRAPH_SECONDS_CAP = 60.0


def _refine_segment_times(segments, words):
    """Snap each segment's start/end inward to its first/last word.

    Whisper pads segment boundaries with the surrounding silence, so
    the gap between two segments understates the actual pause between
    the spoken words. When word timestamps were recorded, trimming each
    segment to its words makes every gap-based paragraph decision (and
    the printed timestamps) measure real silence. Inward-only, so
    segment order and spans stay consistent; segments whose words can't
    be found pass through untouched."""
    if not words:
        return segments
    refined = []
    wi, n_words = 0, len(words)
    for seg in segments:
        s_start, s_end, text = seg[0], seg[1], seg[2]
        while wi < n_words and words[wi][1] <= s_start:
            wi += 1
        first_start, last_end = None, None
        wj = wi
        while wj < n_words and words[wj][0] < s_end:
            if first_start is None:
                first_start = words[wj][0]
            last_end = words[wj][1]
            wj += 1
        wi = wj
        new_start = (first_start
                     if first_start is not None
                     and s_start <= first_start < s_end else s_start)
        new_end = (last_end
                   if last_end is not None
                   and s_start < last_end <= s_end else s_end)
        if new_start < new_end:
            refined.append((new_start, new_end, text))
        else:
            refined.append(seg)
    return refined


# ---- Speaker changes known from the recording ---------------------------
#
# When each speaker has a channel of their own (transcribr/channels.py),
# who is speaking is known for every moment of the recording before the
# first word is transcribed. A paragraph should then never hold two
# people, and splitting at those changes puts more of the speech under
# the right label. Turns are [(start, end, speaker)].

# A run by one speaker shorter than this - a "mm" or "yeah" from the
# other side - does not split a paragraph; it stays with its neighbour.
_SPEAKER_MIN_RUN_S = 0.4
# A segment counts as one speaker's when that speaker has this share of
# its overlap with the turns.
_SPEAKER_CLEAR_SHARE = 0.6


def _speaker_between(turns, start, end, clear=_SPEAKER_CLEAR_SHARE):
    """The speaker holding most of [start, end], when clearly one."""
    tally = {}
    for a, b, k in turns:
        if a >= end:
            break
        o = min(b, end) - max(a, start)
        if o > 0:
            tally[k] = tally.get(k, 0.0) + o
    if not tally:
        return None
    k = max(tally, key=tally.get)
    return k if tally[k] / sum(tally.values()) >= clear else None


def split_at_speaker_changes(segments, words, turns,
                             min_run=_SPEAKER_MIN_RUN_S):
    """Cut any segment that spans a change of speaker into one piece per
    speaker, using its word timings. Local to each segment, so the live
    and batch paths can both apply it and still agree. Segments without
    word timings, or with one speaker, pass through unchanged."""
    if not turns or not words:
        return list(segments)
    turns = sorted(turns)
    out = []
    wi, n = 0, len(words)
    for seg in segments:
        s0, s1 = seg[0], seg[1]
        while wi < n and words[wi][1] <= s0:
            wi += 1
        mine = []
        wj = wi
        while wj < n and words[wj][0] < s1:
            mine.append(words[wj])
            wj += 1
        runs = []                          # [speaker, [words]]
        for w in mine:
            k = _speaker_between(turns, w[0], w[1], clear=0.5)
            if runs and (k is None or k == runs[-1][0]):
                runs[-1][1].append(w)
            else:
                runs.append([k, [w]])
        # absorb runs too short to be a turn of their own
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, (k, ws) in enumerate(runs):
                if ws[-1][1] - ws[0][0] < min_run:
                    j = i - 1 if i > 0 else i + 1
                    if j < i:
                        runs[j][1].extend(ws)
                    else:
                        runs[j][1][:0] = ws
                    del runs[i]
                    changed = True
                    break
        # neighbours left with the same speaker join up again
        merged = []
        for k, ws in runs:
            if merged and merged[-1][0] == k:
                merged[-1][1].extend(ws)
            else:
                merged.append([k, list(ws)])
        if len(merged) < 2:
            out.append(seg)
            continue
        for k, ws in merged:
            text = "".join(str(w[2]) for w in ws).strip()
            if text:
                out.append((ws[0][0], ws[-1][1], text))
    return out


class VoiceBreak:
    """A paragraph break where the voice changes: the pitch at the start
    of a segment differs from the end of the one before by at least
    `min_jump` semitones. A change of speaker typically moves pitch much
    further than a new sentence by the same speaker does.

    `need_sentence_end` limits it to boundaries where the previous
    segment finished a sentence - the case the text rules miss, a full
    stop followed almost at once by a new voice. Measurement comes from
    a transcribr.turns.VoiceMeter, which is local to each segment, so
    the live and batch paths agree."""

    def __init__(self, meter, min_jump=5.0, need_sentence_end=True,
                 min_gap=0.0):
        self.meter = meter
        self.min_jump = min_jump
        self.need_sentence_end = need_sentence_end
        self.min_gap = min_gap

    def __call__(self, prev, curr):
        if prev is None:
            return False
        prev_text = prev[2].rstrip()
        if self.need_sentence_end and not prev_text.endswith(
                (".", "?", "!")):
            return False
        if _is_sentence_continuation(prev_text, curr[2]):
            return False
        if curr[0] - prev[1] < self.min_gap:
            return False
        jump = self.meter.jump(prev, curr)
        return jump is not None and jump >= self.min_jump


# How many segments to look at before deciding whether this engine's
# times carry real silence. It is a property of the engine and its
# settings rather than of the recording, so a sample settles it.
# Measured against the whole-transcript answer over several hundred
# generated transcripts: four segments agree 99.5% of the time and
# eight agree every time, so eight it is. Holding them back costs the
# pane nothing, because they are shown as the provisional tail while
# the judgement is pending - what waits is sealing, not display.
_GAP_SAMPLE_SEGMENTS = 8


class ParagraphStream:
    """Groups segments into paragraphs as they arrive.

    paragraphify() is a thin wrapper over this, so a transcript built
    live segment by segment and the same transcript built in one go
    come out identical. The live review pane rests on that: what you
    watch appear has to be what you would have got by waiting.

    A paragraph can be sealed the moment the next segment starts a new
    one, because _should_break() looks only at the previous segment and
    the current one. Nothing arriving later can reach back and change a
    decision already made - which is what makes it safe to let somebody
    edit a paragraph while the run is still going.

    The one judgement that is not local is whether the engine's gaps
    mean anything (_gaps_are_informative), so when the caller has not
    already decided, the first _GAP_SAMPLE_SEGMENTS segments are held
    back until it can be settled once and frozen.
    """

    def __init__(self, gap_threshold, *, gaps_informative=None,
                 sample_size=_GAP_SAMPLE_SEGMENTS, turns=None,
                 voice=None):
        self.gap_threshold = gap_threshold
        # Who is speaking when, if the recording says (channels): a
        # change of speaker always starts a new paragraph.
        self.turns = sorted(turns) if turns else None
        # A VoiceBreak, when the audio is at hand: a change of voice
        # starts a new paragraph too.
        self.voice = voice
        self.gaps_informative = gaps_informative
        self._sample_size = sample_size
        self._pending = []          # held until gaps_informative settles
        self._current = []          # the paragraph still open
        self._prev = None
        self._para_start = None

    @property
    def tail(self):
        """Everything not yet final, or None.

        Usually the paragraph still open. While the gaps judgement is
        pending it is the held sample instead: those segments may still
        be split across several paragraphs once it settles, so they are
        not final - but there is no reason to hide them, and a pane
        that shows nothing for the first eight segments of a run looks
        broken."""
        if self._pending:
            return list(self._pending)
        return list(self._current) if self._current else None

    def push(self, segments):
        """Feed in newly transcribed segments. Returns the paragraphs
        that are now final - never the open one."""
        sealed = []
        for seg in segments:
            if self.gaps_informative is None:
                self._pending.append(seg)
                if len(self._pending) < self._sample_size:
                    continue
                self._settle()
                sealed.extend(self._absorb(self._pending))
                self._pending = []
                continue
            sealed.extend(self._absorb([seg]))
        return sealed

    def finish(self):
        """End of the run: settle any held sample and close the open
        paragraph. Returns whatever is left to seal."""
        sealed = []
        if self.gaps_informative is None:
            self._settle()
            sealed.extend(self._absorb(self._pending))
            self._pending = []
        if self._current:
            sealed.append(self._current)
            self._current = []
            self._prev = None
            self._para_start = None
        return sealed

    def _speaker_changed(self, seg):
        """A clear change of speaker between the previous segment and
        this one, by the recording's own turns."""
        if not self.turns or self._prev is None:
            return False
        a = _speaker_between(self.turns, self._prev[0], self._prev[1])
        b = _speaker_between(self.turns, seg[0], seg[1])
        return a is not None and b is not None and a != b

    def _settle(self):
        self.gaps_informative = _gaps_are_informative(self._pending)

    def _absorb(self, segments):
        sealed = []
        for seg in segments:
            if self._current:
                forced_by_cap = (
                    self._para_start is not None
                    and seg[1] - self._para_start >= _PARAGRAPH_SECONDS_CAP)
                if forced_by_cap or self._speaker_changed(seg) \
                        or _should_break(self._prev, seg,
                                         self.gap_threshold,
                                         self.gaps_informative) \
                        or (self.voice is not None
                            and self.voice(self._prev, seg)):
                    sealed.append(self._current)
                    self._current = []
                    self._para_start = None
            if self._para_start is None:
                self._para_start = seg[0]
            self._current.append(seg)
            self._prev = seg
        return sealed


def paragraphify(segments, gap_threshold: float, words=None, turns=None,
                 voice=None):
    """Group segments into paragraphs on purely programmatic cues
    (see _should_break). `words`, when word timestamps were recorded,
    sharpens the gap measurements via _refine_segment_times. `turns`,
    when the recording says who spoke when (channels), splits segments
    and paragraphs wherever the speaker changes.

    The whole set is in hand here, so the gaps judgement is made over
    all of it rather than from a sample - which is why this and
    ParagraphStream agree on a finished transcript."""
    segments = _refine_segment_times(segments, words or [])
    if turns:
        segments = split_at_speaker_changes(segments, words or [], turns)
    stream = ParagraphStream(
        gap_threshold, gaps_informative=_gaps_are_informative(segments),
        turns=turns, voice=voice)
    paragraphs = stream.push(segments)
    paragraphs.extend(stream.finish())
    return paragraphs


def _disclaimer_text(verified_by=None) -> str:
    """The line appended to every saved transcript. Unverified output
    carries a warning; once a human certifies the transcript from the
    review pane, it names them instead."""
    origin = ("Transcribed using Transcribr "
              "[https://github.com/jamesleaver/Transcribr].")
    if verified_by:
        return f"{origin} This transcript has been verified by {verified_by}."
    return (f"{origin} If this text has not been deleted, the accuracy of "
            "this transcript may not have been checked by a human.")


def render(paragraphs, *, show_timestamp=True, title=None, speakers=None,
           hide_ts=None) -> str:
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
        # Blank lines separate blocks in this format, so a body that
        # contains one would be read back as two paragraphs. The body is
        # one paragraph by definition; collapse its whitespace to match.
        body = " ".join(" ".join(seg[2].split()) for seg in para).strip()
        speaker = speakers[i] if speakers and i < len(speakers) else None
        hidden = bool(hide_ts and i < len(hide_ts) and hide_ts[i])
        stamped = show_timestamp and not hidden
        block_lines = []
        if speaker and speaker != last_speaker:
            block_lines.append(f"{speaker}:")
        elif speaker is None and last_speaker is not None:
            # Transition from a named speaker to an unattributed paragraph.
            # Emit an explicit marker so re-parsing this file doesn't carry
            # the previous speaker over.
            block_lines.append(f"{UNATTRIBUTED_LABEL}:")
        elif i == 0 and speaker is None and not stamped and not title:
            # A first paragraph with no title above it, no speaker label
            # and no timestamp is, on the page, indistinguishable from a
            # title - and the parser read it back as one, losing the
            # paragraph. The marker is the one piece of information the
            # file was missing; it costs a line and says something true.
            block_lines.append(f"{UNATTRIBUTED_LABEL}:")
        elif not block_lines and not stamped and body.endswith(":"):
            # A body that would open its own block and ends in a colon
            # reads exactly like a speaker label, and came back as one -
            # the paragraph vanished. "And then he turned to me and
            # said:" is an ordinary way for spoken evidence to end.
            # Repeating the label above it costs nothing and settles it.
            block_lines.append(f"{speaker or UNATTRIBUTED_LABEL}:")
        if stamped:
            block_lines.append(f"{format_timestamp(start)}  {body}")
        else:
            block_lines.append(body)
        out_lines.append("\n".join(block_lines))
        last_speaker = speaker

    return "\n\n".join(out_lines) + "\n"
