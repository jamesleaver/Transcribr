"""The transcription run itself, and the analysis passes over its
output (vocabulary, hallucination and dead-air detection)."""

from pathlib import Path
import contextlib
import re
import threading
import time
import traceback

from .errors import (
    DiarizationUnavailable, _CancelledByUser, _EngineNotAvailable
)
from .platformutil import _log
from .paragraphs import (
    AUDIO_SAMPLE_RATE, _format_duration, _have_pyav, decode_audio_16k,
    describe_non_audio_file, format_timestamp, paragraphify, VoiceBreak
)
from .live import LiveTranscript
from .turns import VoiceMeter
from .writers import _write_extra_formats, write_paragraphs_to_file
from .engines import _mlx_repo_for
from .diarize import label_paragraphs, speaker_turns
from .channels import separated_channel_turns


class _QueueWriter:
    """File-like that forwards writes to a queue as ('log', text) messages."""
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(("log", text))

    def flush(self):
        pass


# Approximate on-disk download sizes per model, used only for first-run
# messaging before the real byte totals arrive from the downloader.
_MODEL_APPROX_MB = {
    "tiny.en": 75, "tiny": 75,
    "base.en": 145, "base": 145,
    "small.en": 480, "small": 480,
    "medium.en": 1500, "medium": 1500,
    "large-v1": 2900, "large-v2": 2900, "large-v3": 2900, "large": 2900,
    "turbo": 1600, "large-v3-turbo": 1600,
}


def _humanize_bytes(n):
    """Compact human-readable byte count, e.g. '1.6 GB', '480 MB'."""
    n = float(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


# '[mm:ss.sss --> mm:ss.sss] text' - the line openai-whisper and
# mlx-whisper both print when verbose=True. Parsed to drive the ETA/
# progress readout.
_SEGMENT_LINE_RE = re.compile(
    r"\[(\d+):(\d+(?:\.\d+)?)\s*-->\s*(\d+):(\d+(?:\.\d+)?)\]\s*(.*)")


class _ProgressWriter:
    """File-like that mirrors an engine's verbose stdout/stderr to the log
    queue while parsing segment lines to emit 'eta' progress events.

    Both openai-whisper and mlx-whisper print '[mm:ss --> mm:ss] text'
    lines as they decode (with verbose=True), so a single parser drives
    the progress bar for both. If `captured` is a list, parsed segments
    are appended to it - openai-whisper relies on that to salvage a
    partial transcript when the user hits Stop mid-run."""

    def __init__(self, q, audio_duration, transcribe_start, captured=None):
        self.q = q
        self._buf = ""
        self.audio_duration = audio_duration
        self.transcribe_start = transcribe_start
        self.captured = captured

    def write(self, text):
        if not text:
            return
        self.q.put(("log", text))
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            m = _SEGMENT_LINE_RE.search(line)
            if not m:
                continue
            sm, ss, em, es, body = m.groups()
            start = int(sm) * 60 + float(ss)
            end = int(em) * 60 + float(es)
            body = body.strip()
            if not body:
                continue
            if self.captured is not None:
                self.captured.append((start, end, body))
            if self.audio_duration and end >= 5.0:
                wall = time.time() - self.transcribe_start
                if wall > 0:
                    speed = end / wall
                    remaining = max(0.0, self.audio_duration - end)
                    eta = remaining / speed if speed > 0 else 0
                    self.q.put(("eta", {
                        "audio_done": end,
                        "audio_total": self.audio_duration,
                        "wall_elapsed": wall,
                        "eta_seconds": eta,
                        "speed": speed,
                    }))

    def flush(self):
        pass


class _DownloadMonitor:
    """Surfaces model downloads that would otherwise look like a frozen
    'Loading model...'.

    Every engine's downloader (openai-whisper's own fetch, and the
    huggingface_hub fetch behind faster-whisper and mlx-whisper) reports
    progress through a tqdm byte-scale bar. This context manager patches
    tqdm so those bars are forwarded to `on_progress(info)` - a dict of
    {model, downloaded, total, speed}. The transcription worker routes
    that onto its queue as ('download', ...) messages; the standalone
    model manager republishes it as a `model_progress` SSE event. A
    one-line announcement (and completion note) goes to `on_log(text)`
    when supplied. Non-download bars (e.g. tiny config files) are ignored
    via a minimum-size filter.

    It also honours cancel_event: cancelling during a download raises
    _CancelledByUser, aborting the fetch instead of forcing the user to
    wait for gigabytes."""

    _MIN_TOTAL = 1024 * 1024  # ignore sub-1 MB bars (configs, tokenizers)

    def __init__(self, cancel_event, model_name, *,
                 on_progress, on_log=None):
        self.cancel_event = cancel_event
        self.model_name = model_name
        self.on_progress = on_progress
        self.on_log = on_log or (lambda _text: None)
        self._bars = {}            # id(bar) -> (downloaded, total)
        self._tqdm = None
        self._orig_update = None
        self._orig_close = None
        self._last_emit = 0.0
        self._last_done = 0
        self._last_speed_t = 0.0
        self._announced = False

    def _is_byte_bar(self, bar):
        unit = getattr(bar, "unit", "") or ""
        total = getattr(bar, "total", None) or 0
        return "B" in unit and total >= self._MIN_TOTAL

    def _snapshot(self):
        downloaded = sum(d for d, _ in self._bars.values())
        total = sum(t for _, t in self._bars.values())
        return downloaded, total

    def _emit(self, force=False):
        now = time.time()
        if not force and now - self._last_emit < 0.25:
            return
        downloaded, total = self._snapshot()
        if total <= 0:
            return
        speed = 0.0
        if self._last_speed_t:
            dt = now - self._last_speed_t
            if dt > 0:
                speed = max(0.0, (downloaded - self._last_done) / dt)
        self._last_speed_t = now
        self._last_done = downloaded
        self._last_emit = now
        self.on_progress({
            "model": self.model_name,
            "downloaded": downloaded,
            "total": total,
            "speed": speed,
        })

    def __enter__(self):
        try:
            import tqdm
        except ImportError:
            return self          # no tqdm - degrade to no-op
        self._tqdm = tqdm
        monitor = self
        self._orig_update = tqdm.tqdm.update
        self._orig_close = tqdm.tqdm.close

        def patched_update(bar, n=1):
            result = monitor._orig_update(bar, n)
            if monitor.cancel_event.is_set():
                raise _CancelledByUser()
            if monitor._is_byte_bar(bar):
                monitor._bars[id(bar)] = (
                    getattr(bar, "n", 0) or 0, getattr(bar, "total", 0) or 0)
                if not monitor._announced:
                    monitor._announced = True
                    approx = _MODEL_APPROX_MB.get(monitor.model_name)
                    size = f" (~{approx / 1000:.1f} GB)" if approx and \
                        approx >= 1000 else (f" (~{approx} MB)" if approx
                                             else "")
                    monitor.on_log(
                        f"Downloading model '{monitor.model_name}'{size} - "
                        "first use only, then it's cached locally...\n")
                monitor._emit()
            return result

        def patched_close(bar):
            if monitor._is_byte_bar(bar):
                total = getattr(bar, "total", 0) or 0
                monitor._bars[id(bar)] = (total, total)
                monitor._emit(force=True)
            return monitor._orig_close(bar)

        tqdm.tqdm.update = patched_update
        tqdm.tqdm.close = patched_close
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._tqdm is not None:
            self._tqdm.tqdm.update = self._orig_update
            self._tqdm.tqdm.close = self._orig_close
        if self._announced and exc_type is None:
            _, total = self._snapshot()
            self.on_log(f"  download complete ({_humanize_bytes(total)})\n")
        return False


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


def _channel_turns(params, q, cancel_event=None):
    """Who spoke when, if each speaker has a channel of their own - or
    None. Worked out once per run and kept in params, before any
    paragraph is formed, so paragraphs can break wherever the speaker
    changes (live and one-pass alike) and speaker labelling can reuse
    it. A mono file is dismissed from its header, so the common case
    costs nothing."""
    if "_channel_turns" in params:
        return params["_channel_turns"]
    turns = None
    try:
        found = separated_channel_turns(params["input"], cancel_event)
    except Exception:
        _log("channel analysis failed:\n" + traceback.format_exc())
        found = None
    if found is not None:
        turns, verdict = found
        q.put(("log", f"\nSpeakers are on separate channels "
                      f"({len(verdict['used'])} in use) - paragraphs will "
                      f"break wherever the speaker changes.\n"))
    params["_channel_turns"] = turns
    params["_channel_verdict"] = found[1] if found else None
    return turns


# Semitones of pitch change, after a finished sentence, that start a new
# paragraph. 4 keeps most of the gain of 3 with fewer extra breaks
# when one person talks on.
VOICE_BREAK_SEMITONES = 4.0


def _voice_break(params):
    """The voice-change paragraph rule for this run, or None when it is
    switched off or there is no decoded audio to measure. Cached in
    params, so the live and one-pass paths share one meter."""
    if not params.get("voice_breaks", True):
        return None
    if "_voice_break" not in params:
        audio = params.get("_audio")
        params["_voice_break"] = (
            VoiceBreak(VoiceMeter(audio), min_jump=VOICE_BREAK_SEMITONES)
            if audio is not None else None)
    return params["_voice_break"]


def _dead_air_spans(params, q):
    """Stretches of the recording with no audio at all.

    Scanned from the recording rather than from the transcript, so it
    can happen before the engine has produced a word - which is what
    lets phantom text be dropped as it arrives instead of afterwards."""
    try:
        audio = params.get("_audio")
        if audio is None:
            audio = decode_audio_16k(params["input"])
            if audio is not None:
                params["_audio"] = audio
        if audio is None:
            return []
        spans = detect_silences(audio)
    except Exception:
        _log("dead-air scan failed:\n" + traceback.format_exc())
        return []
    if spans:
        q.put(("log",
               f"\nFound {len(spans)} span(s) with no recorded audio - "
               "marking them in the transcript.\n"))
    return spans


class _Diarization:
    """Speaker detection running alongside the transcription.

    It needs the whole recording, and the whole recording is a file
    that exists before the first segment does - so there is no reason
    for it to wait for the transcript. It is a different model on a
    different thread, and both spend their time in native code that
    releases the GIL, so the pass is effectively free in wall-clock
    terms. Labels land when the paragraphs they belong to are ready.
    """

    def __init__(self, params, q, cancel_event):
        self.params = params
        self.q = q
        self.cancel_event = cancel_event
        self.turns = None
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="transcribr-diarize")

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        params, q = self.params, self.q
        try:
            audio = params.get("_audio")
            if audio is None:
                audio = decode_audio_16k(params["input"])
            self.turns = speaker_turns(params, audio, q, self.cancel_event)
        except _CancelledByUser:
            self.error = "stopped by user"
        except DiarizationUnavailable as e:
            self.error = str(e)
        except Exception as e:
            _log("diarization failed:\n" + traceback.format_exc())
            self.error = f"{type(e).__name__}: {e}"

    def labels_for(self, paragraphs, timeout=None):
        """Wait for the pass to finish and label `paragraphs`, or None.

        By the time this is called the transcription has finished, and
        detection is usually long done - so the wait is normally
        nothing. On a stopped run it is given a deadline instead:
        labels are worth a moment, but not worth holding up the
        partial transcript somebody asked to keep."""
        self._thread.join(timeout)
        if self._thread.is_alive():
            self.q.put(("log", "\nSpeaker detection didn't finish before "
                               "the stop - saving without labels.\n"))
            return None
        if self.turns is None:
            if self.error == "stopped by user":
                self.q.put(("log", "\n[Speaker detection skipped - stopped "
                                   "by user]\n"))
            elif self.error:
                self.q.put(("log",
                            f"\nSpeaker detection skipped: {self.error}\n"))
            return None
        letters = label_paragraphs(paragraphs, self.turns)
        labelled = sum(1 for L in letters if L)
        self.q.put(("log",
                    f"  labelled {labelled} of {len(paragraphs)} "
                    "paragraphs (uncertain ones are left for review)\n"))
        return letters


class _TranscriptBuilder:
    """Accumulates the finished paragraph list, handing each one over as
    soon as it is final when `emit` is given."""

    def __init__(self, params, spans, emit=None):
        self.emit = emit
        self.live = LiveTranscript(params["gap"], silence_spans=spans,
                                   turns=params.get("_channel_turns"),
                                   voice=_voice_break(params))
        self.paragraphs = []

    def push(self, segments, words=None):
        sealed = self.live.push(segments, words=words)
        self.paragraphs.extend(sealed)
        if self.emit is not None:
            self.emit(sealed, self.live.tail)

    def finish(self):
        sealed = self.live.finish()
        self.paragraphs.extend(sealed)
        if self.emit is not None:
            self.emit(sealed, None)
        return self.paragraphs

    @property
    def dropped(self):
        return self.live.dropped


def _run_parakeet(params, q, cancel_event, on_segment=None):
    from . import parakeet
    try:
        return parakeet.run(params, q, cancel_event, on_segment)
    except parakeet.ParakeetUnavailable as e:
        raise _EngineNotAvailable(str(e))


def runner_for(params, q=None, default="whisper"):
    """The function that transcribes for these params. The Parakeet
    model has its own runner whatever the engine; asked for a language
    other than English, where Parakeet cannot help, it falls back to
    Whisper small on the chosen engine."""
    from .engines import PARAKEET_MODEL
    if params.get("model") == PARAKEET_MODEL:
        if params.get("language") in (None, "en"):
            return _run_parakeet
        params["model"] = "small"
        if q is not None:
            q.put(("log", "Parakeet knows English only - using Whisper "
                          "'small' for this language.\n"))
    runners = {
        "whisper": _run_openai_whisper,
        "faster": _run_faster_whisper,
        "mlx": _run_mlx_whisper,
    }
    return runners.get(params.get("engine"), runners[default])


def transcribe_worker(params, q, cancel_event):
    """Background-thread entry point. Dispatches to the chosen engine's
    runner, then handles the common post-processing: speaker detection,
    paragraphify, review-or-direct-save, extra output formats."""
    runner = runner_for(params, q)

    # Decode once up front (PyAV). Every engine accepts the raw samples,
    # which removes their need for an ffmpeg binary, and the speaker
    # detector reuses the same array. On failure the runners receive the
    # file path and decode with whatever their engine ships.
    audio = decode_audio_16k(params["input"])
    if audio is not None:
        params["_audio"] = audio
        duration = len(audio) / AUDIO_SAMPLE_RATE
        q.put(("log", f"Decoded audio: {_format_duration(duration)}\n"))
        if not params.get("audio_duration"):
            params["audio_duration"] = duration
    elif _have_pyav():
        # PyAV is present but couldn't decode the file. The engine's own
        # loader would fail the same way with a raw ffmpeg traceback, so
        # explain the actual problem instead - the usual cause is a
        # non-audio file wearing an audio extension.
        reason = describe_non_audio_file(params["input"])
        if reason:
            q.put(("error",
                   f"Cannot transcribe {Path(params['input']).name}: "
                   f"{reason}.\n\n"
                   "Check that the file really is an audio or video "
                   "recording - if it came out of an export or download, "
                   "the export may have produced the wrong file. Opening "
                   "it in a media player is a quick way to confirm."))
            return

    # Tell the user which word-timing mode is in effect - on mlx the
    # alignment is the slow part, so this explains a longer run (or a
    # faster one when it's skipped).
    if params.get("word_timestamps"):
        if params.get("engine") == "mlx":
            q.put(("log",
                   "Word-level timing is ON (mlx): this adds a slower "
                   "alignment pass. Turn it off in Settings for faster "
                   "runs.\n"))
    else:
        q.put(("log",
               "Word-level timing is off - faster, but no confidence "
               "highlighting for this run (change it in Settings).\n"))

    # Everything that can be known before the engine has said a word is
    # worked out now: where the recording has no sound, and who is
    # speaking. Both need the whole recording, and the whole recording
    # is a file that already exists - so neither has any reason to wait
    # for the transcript.
    spans = _dead_air_spans(params, q)
    _channel_turns(params, q, cancel_event)
    diarization = (_Diarization(params, q, cancel_event).start()
                   if params.get("diarize") else None)

    # A run that ends in the review pane reports paragraphs as they are
    # finished, so the pane can fill in while this is still going. The
    # messages are additive: a client that ignores them sees exactly
    # what it saw before, at the end.
    live = bool(params.get("review_before_save"))
    if live:
        q.put(("transcript_started", {
            "out_path": str(Path(params["output"])),
            "show_timestamp": params.get("show_timestamp", True),
            "title": params.get("title"),
            "output_format": params.get("output_format", "txt"),
            "audio_path": params["input"],
            "colour_speakers": bool(params.get("colour_speakers")),
            "extra_formats": params.get("extra_formats") or [],
            "model": params.get("model"),
        }))

    def emit(sealed, tail):
        q.put(("paragraphs_grew", {"sealed": sealed, "tail": tail}))

    builder = _TranscriptBuilder(params, spans, emit=emit if live else None)
    streamed = []

    def on_segment(segment, words=None):
        streamed.append(segment)
        builder.push([segment], words=words)

    try:
        segments, result, used_partial = runner(params, q, cancel_event,
                                                on_segment=on_segment)
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

    # Engines that cannot report as they go (openai-whisper, mlx) hand
    # everything back at the end; push whatever the callback did not
    # already see, with the word timings the batch path would have used.
    leftover = segments[len(streamed):]
    if leftover:
        builder.push(leftover, words=_extract_word_conf(result))
    paragraphs = builder.finish()
    if builder.dropped:
        q.put(("log",
               f"  removed {builder.dropped} phantom segment(s) the model "
               "invented inside the silence\n"))
    # Detection has had the whole run to finish, so this is normally no
    # wait at all. Marker paragraphs sit over dead air, where no voice
    # overlaps them, so they come back unlabelled of their own accord.
    para_letters = (diarization.labels_for(
                        paragraphs,
                        timeout=5.0 if cancel_event.is_set() else None)
                    if diarization is not None else None)
    out_path = Path(params["output"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if params.get("review_before_save"):
        # Word timings are always recorded, so the review pane always
        # gets confidence data; its own toggle governs the shading.
        word_conf = _extract_word_conf(result) or None
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
                "preset_speakers": para_letters,
                "diarized": para_letters is not None,
                "colour_speakers": bool(params.get("colour_speakers")),
                "model": params.get("model"),
            },
        ))
        return

    # Unattended save: detected speakers go in as generic names the
    # user can refine later from the Library's review pane.
    speaker_names = None
    if para_letters is not None:
        speaker_names = [f"Speaker {L}" if L else None
                         for L in para_letters]
    try:
        write_paragraphs_to_file(
            paragraphs, out_path,
            show_timestamp=params.get("show_timestamp", True),
            title=params.get("title"),
            output_format=params.get("output_format", "txt"),
            speakers=speaker_names,
            audio_path=params["input"],
            speaker_slots=para_letters,
            colour_speakers=bool(params.get("colour_speakers")),
            template=params.get("doc_template", "plain"),
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


# --- names and terms --------------------------------------------------
#
# Proper nouns are where a transcript is most often wrong and most
# expensive to fix by hand: a surname spelled three ways across forty
# paragraphs is forty separate corrections. The engine has no idea
# whether it heard Cavill, Cavil or Carvill, and will use all three in
# one document without noticing.
#
# So: find the candidates, group the ones that look like the same word,
# and let the reviewer settle each once.

# Capitalised words that are almost never names. Titles are deliberately
# absent - "Constable Doyle" is exactly the sort of term worth catching.
_TERM_STOPWORDS = frozenset("""
    a an and are as at be been but by for from had has have he her him his
    i if in is it its me my no not of on or our she so that the their them
    then there these they this to was we were what when where which who
    why will with would you your yes okay oh ah um uh well just look now
    get got do does did can could should must may might shall
    good right sorry thank thanks please come go come on
""".split())

_TERM_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Titles worth keeping with the name and worth looking behind: the
# surname in "Constable Doyle" is the thing that also appears alone.
_TERM_TITLES = frozenset("""
    mr mrs ms miss dr prof professor constable sergeant sgt senior
    detective det inspector superintendent judge justice magistrate
    officer sir madam lord lady his her honour
""".split())


def _capitalised(word):
    return word[:1].isupper() and not word.isupper()


def _term_key(term):
    """Normalised form for grouping: case and punctuation folded away."""
    return re.sub(r"[^a-z]", "", term.lower())


def extract_terms(paragraphs, min_count=1):
    """Candidate names and terms in a transcript, most frequent first.

    Returns [{"term", "count", "variants": [{"term", "count"}, ...]}].
    Runs of capitalised words are kept together, so "Brunswick Street"
    is one term rather than two. Where a run begins with a title, the
    name behind it is counted separately as well - "Constable Doyle"
    also yields "Doyle", which is what groups with a stray "Doyal"
    elsewhere in the transcript.

    Near-spellings are grouped, so the reviewer sees "Cavill" with
    "Cavil" and "Carvill" beneath it and settles all three at once.

    Nothing is corrected automatically. A transcript is evidence;
    guessing at the names in it is not the software's to do.
    """
    counts = {}

    def add(term):
        if term:
            counts[term] = counts.get(term, 0) + 1

    for para in paragraphs:
        body = " ".join(seg[2] for seg in para)
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            run = []
            for m in _TERM_WORD_RE.finditer(sentence):
                word = m.group(0)
                if _capitalised(word) and word.lower() not in _TERM_STOPWORDS:
                    run.append(word)
                    continue
                _flush_run(run, add)
                run = []
            _flush_run(run, add)

    groups = []
    for term, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        key = _term_key(term)
        for g in groups:
            if _looks_like_same_term(key, g["key"]):
                g["variants"].append({"term": term, "count": n})
                g["count"] += n
                break
        else:
            groups.append({"key": key, "term": term, "count": n,
                           "variants": [{"term": term, "count": n}]})

    out = [{"term": g["term"], "count": g["count"],
            "variants": sorted(g["variants"],
                               key=lambda v: (-v["count"], v["term"]))}
           for g in groups if g["count"] >= min_count]
    out.sort(key=lambda g: (-g["count"], g["term"]))
    return out


def _flush_run(run, add):
    """Record a run of capitalised words as one term, plus the name
    behind any leading title."""
    if not run:
        return
    add(" ".join(run))
    if len(run) > 1 and run[0].lower().strip(".") in _TERM_TITLES:
        rest = [w for w in run[1:]
                if w.lower().strip(".") not in _TERM_TITLES]
        if rest:
            add(" ".join(rest))


def _looks_like_same_term(a, b):
    """Whether two normalised terms are plausibly the same word spelled
    differently.

    Set where it catches the mishearings that matter - Doyle/Doyal,
    Smith/Smyth, Cavill/Carvill - which means it will occasionally put
    two genuinely different names together (Mason/Macon sits at the same
    similarity as Smith/Smyth; nothing in the spelling separates them).
    That is why the panel lists every spelling a correction will rewrite
    and asks before doing it: the reviewer, who knows the case, is the
    one placed to tell those apart."""
    if a == b:
        return True
    if not a or not b or a[0] != b[0]:
        return False
    if abs(len(a) - len(b)) > max(2, min(len(a), len(b)) // 3):
        return False
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.80


# --- hallucination detection ------------------------------------------
# Whisper (and similar models) loop on silence or noise, emitting the
# same word or short phrase over and over ("you you you…", "Thank you.
# Thank you. Thank you.") or repeating a whole line across paragraphs.
# These heuristics flag the obvious loops so the review pane can suggest
# re-transcribing them. Deliberately conservative: the aim is runaway
# repetition, not the ordinary "Yes. Yes." of a real exchange.

_WORD_RE = re.compile(r"\w+(?:['’]\w+)?", re.UNICODE)

# A phrase (1-4 words) repeated at least this many times in a row, when
# it also dominates the paragraph, reads as a loop rather than emphasis.
HALLUCINATION_MIN_REPEATS = 3
HALLUCINATION_COVERAGE = 0.6

# A LONGER phrase repeated back to back is a loop wherever it sits - it
# need not dominate the paragraph. Real decoders loop on whole clauses
# ("there was a car in the drive there was a car in the drive ...")
# mid-paragraph, which the rule above never saw. Shorter phrases need
# more repeats before they count, because people do stammer: "it is a,
# it is a, it is a, it is a" is genuine in live speech.
HALLUCINATION_LONG_PHRASE_MAX = 15
HALLUCINATION_LONG_REPEATS = {3: 5}     # phrase length -> repeats needed
HALLUCINATION_LONG_REPEATS_DEFAULT = 3  # for 4..MAX words

# The engine's own confidence as a second hint: a paragraph most of
# whose words the engine scored very low (the same < 0.35 band the
# review pane shades red) is likely garbled or invented. Kept
# conservative - a solid majority of several words - so a soft-spoken
# but genuine passage isn't flagged.
HALLUCINATION_LOWCONF_PROB = 0.35
HALLUCINATION_LOWCONF_FRACTION = 0.6
HALLUCINATION_LOWCONF_MINWORDS = 4


def _normalise_words(text):
    return _WORD_RE.findall((text or "").lower())


def _is_fully_periodic(words):
    """True if the whole word list is a phrase repeated k>=2 times (a
    paragraph that is nothing but a loop), and it is long enough that
    this can't be an ordinary short repeat like 'Thank you. Thank you.'."""
    n = len(words)
    if n < 5:
        return False
    for d in range(1, n // 2 + 1):
        if n % d == 0:
            base = words[:d]
            if all(words[i:i + d] == base for i in range(0, n, d)):
                return True
    return False


def _has_repetition_loop(text):
    """True if `text` holds a model loop: an immediately-repeated 1-4
    word phrase that repeats three or more times and covers most of the
    paragraph, a paragraph that is nothing but two-or-more repeats of
    one phrase, or a longer phrase looping anywhere in it
    (_has_long_phrase_loop)."""
    words = _normalise_words(text)
    n = len(words)
    if n < HALLUCINATION_MIN_REPEATS:
        return False
    if _is_fully_periodic(words):
        return True
    for size in range(1, 5):
        for i in range(0, n - size + 1):
            phrase = words[i:i + size]
            reps = 1
            j = i + size
            while words[j:j + size] == phrase:
                reps += 1
                j += size
            if reps >= HALLUCINATION_MIN_REPEATS and \
                    reps * size >= max(HALLUCINATION_MIN_REPEATS,
                                       n * HALLUCINATION_COVERAGE):
                return True
    return _has_long_phrase_loop(words)


def _has_long_phrase_loop(words):
    """True if a phrase of 3..HALLUCINATION_LONG_PHRASE_MAX words
    repeats back to back often enough to be a decoder loop, anywhere in
    the paragraph."""
    n = len(words)
    for size in range(3, HALLUCINATION_LONG_PHRASE_MAX + 1):
        need = HALLUCINATION_LONG_REPEATS.get(
            size, HALLUCINATION_LONG_REPEATS_DEFAULT)
        for i in range(0, n - size * need + 1):
            phrase = words[i:i + size]
            reps, j = 1, i + size
            while words[j:j + size] == phrase:
                reps += 1
                j += size
            if reps >= need:
                return True
    return False


def _mostly_low_confidence(word_probs):
    """True if a solid majority of a paragraph's words carry very low
    engine confidence - a hint the passage is garbled or invented."""
    probs = [p for p in (word_probs or []) if p is not None]
    if len(probs) < HALLUCINATION_LOWCONF_MINWORDS:
        return False
    low = sum(1 for p in probs if p < HALLUCINATION_LOWCONF_PROB)
    return low / len(probs) >= HALLUCINATION_LOWCONF_FRACTION


def detect_hallucinations(paragraphs, confidences=None):
    """Return the sorted indices of paragraphs that look like engine
    hallucination: a runaway repeated word/phrase within a paragraph, a
    non-trivial line repeated verbatim across adjacent paragraphs, or
    (when `confidences` - a per-paragraph list of word probabilities -
    is given) a paragraph the engine itself scored mostly very low."""
    bodies = [" ".join(seg[2] for seg in para).strip()
              for para in paragraphs]
    flagged = set()
    for i, body in enumerate(bodies):
        if _has_repetition_loop(body):
            flagged.add(i)
        elif confidences is not None and i < len(confidences) \
                and _mostly_low_confidence(confidences[i]):
            flagged.add(i)
    # Consecutive (normalised-)identical, non-trivial paragraphs.
    for i in range(1, len(bodies)):
        prev = _normalise_words(bodies[i - 1])
        cur = _normalise_words(bodies[i])
        if cur and cur == prev and len(cur) >= 3:
            flagged.add(i - 1)
            flagged.add(i)
    return sorted(flagged)


# --- dead-air detection -----------------------------------------------
# True "no recorded audio" (a camera whose microphone switches on late,
# a muted channel) sits at or near digital silence - far below even a
# quiet room's noise floor - so a conservative RMS gate separates it
# from soft speech. Only spans long enough to matter are reported.

SILENCE_WINDOW_SECS = 0.5
SILENCE_RMS = 1e-3            # about -60 dBFS
SILENCE_MIN_SPAN_SECS = 5.0


def detect_silences(audio, rate=AUDIO_SAMPLE_RATE,
                    min_span=SILENCE_MIN_SPAN_SECS,
                    threshold=SILENCE_RMS):
    """Scan a mono float sample array for spans with essentially no
    recorded audio. Returns [(start_sec, end_sec), ...] of merged spans
    at least min_span long; a trailing span runs to the file's end."""
    import numpy as np
    samples = np.asarray(audio, dtype=np.float32)
    n = int(samples.shape[0])
    w = max(1, int(rate * SILENCE_WINDOW_SECS))
    m = n // w
    if m == 0:
        return []
    rms = np.sqrt(np.mean(np.square(samples[:m * w].reshape(m, w)),
                          axis=1))
    quiet = rms < threshold
    runs = []
    start = None
    for i, flag in enumerate(quiet):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, m))
    win = w / float(rate)
    total = n / float(rate)
    out = []
    for a, b in runs:
        s = a * win
        e = total if b == m else b * win
        if e - s >= min_span:
            out.append((round(s, 2), round(e, 2)))
    return out


def _drop_segments_in_silence(segments, spans):
    """Whisper models invent text inside dead air. Drop segments whose
    time range lies (almost) entirely inside a detected no-audio span -
    at least 90% overlap, so real speech brushing a span's edge is
    never touched. Returns (kept_segments, dropped_count)."""
    kept = []
    dropped = 0
    for seg in segments:
        s, e = float(seg[0]), float(seg[1])
        length = max(e - s, 1e-6)
        phantom = any(
            (min(e, b) - max(s, a)) / length >= 0.9
            for a, b in spans)
        if phantom:
            dropped += 1
        else:
            kept.append(seg)
    return kept, dropped


def _splice_no_audio_markers(paragraphs, letters, spans):
    """Weave "[No audio from A to B]" marker paragraphs (single
    pseudo-segments spanning the dead air) into the paragraph list in
    time order. `letters` (per-paragraph speaker labels, possibly None)
    stays parallel; markers never get a speaker."""
    if not spans:
        return paragraphs, letters

    def plain(t):
        return format_timestamp(t)[1:-1]

    markers = [
        [(a, b, f"[No audio from {plain(a)} to {plain(b)}]")]
        for a, b in spans]
    lts = letters if letters is not None else [None] * len(paragraphs)
    merged = []
    mlts = []
    pi = mi = 0
    while pi < len(paragraphs) or mi < len(markers):
        take_marker = (
            mi < len(markers)
            and (pi >= len(paragraphs)
                 or markers[mi][0][0] <= paragraphs[pi][0][0]))
        if take_marker:
            merged.append(markers[mi])
            mlts.append(None)
            mi += 1
        else:
            merged.append(paragraphs[pi])
            mlts.append(lts[pi])
            pi += 1
    return merged, (mlts if letters is not None else None)


def _paragraphs_with_speakers(segments, result, params, q, cancel_event):
    """Group segments into paragraphs with the programmatic heuristics,
    then - when the experimental speaker detection is on - suggest a
    label for each paragraph. The paragraphs are identical either way:
    detection only labels them, never reshapes them, and any failure
    just leaves the labels off with a logged warning.

    Before grouping, the recording is scanned for dead air (no recorded
    audio at all): phantom text transcribed inside those spans is
    removed and each span becomes a "[No audio from A to B]" marker
    paragraph, so the transcript states plainly where the recording has
    sound and where it doesn't."""
    spans = []
    try:
        audio = params.get("_audio")
        if audio is None:
            audio = decode_audio_16k(params["input"])
            if audio is not None:
                params["_audio"] = audio
        if audio is not None:
            spans = detect_silences(audio)
    except Exception:
        _log("dead-air scan failed:\n" + traceback.format_exc())
    if spans:
        segments, dropped = _drop_segments_in_silence(segments, spans)
        q.put(("log",
               f"\nFound {len(spans)} span(s) with no recorded audio - "
               "marking them in the transcript.\n"))
        if dropped:
            q.put(("log",
                   f"  removed {dropped} phantom segment(s) the model "
                   "invented inside the silence\n"))

    words = _extract_word_conf(result) if result else []
    turns = _channel_turns(params, q, cancel_event)
    paragraphs = paragraphify(segments, params["gap"], words=words,
                              turns=turns, voice=_voice_break(params))
    para_letters = None
    if params.get("diarize"):
        try:
            audio = params.get("_audio")
            if audio is None:
                audio = decode_audio_16k(params["input"])
            turns = speaker_turns(params, audio, q, cancel_event)
            para_letters = label_paragraphs(paragraphs, turns)
            labelled = sum(1 for L in para_letters if L)
            q.put(("log",
                   f"  labelled {labelled} of {len(paragraphs)} "
                   "paragraphs (uncertain ones are left for review)\n"))
        except _CancelledByUser:
            q.put(("log", "\n[Speaker detection skipped - stopped by "
                          "user]\n"))
        except DiarizationUnavailable as e:
            q.put(("log", f"\nSpeaker detection skipped: {e}\n"))
        except Exception as e:
            _log("diarization failed:\n" + traceback.format_exc())
            q.put(("log",
                   f"\nSpeaker detection failed - continuing without "
                   f"it.\n  {type(e).__name__}: {e}\n"))
    return _splice_no_audio_markers(paragraphs, para_letters, spans)


def _accepts_option(fn, name):
    """Whether `fn` takes a keyword argument called `name`."""
    import inspect
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _run_openai_whisper(params, q, cancel_event, on_segment=None):
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
    q.put(("status", {"stage": "loading",
                      "text": f"Loading model '{params['model']}'..."}))
    t0 = time.time()
    with _DownloadMonitor(
            cancel_event, params["model"],
            on_progress=lambda d: q.put(("download", d)),
            on_log=lambda t: q.put(("log", t))):
        model = whisper.load_model(params["model"])
    q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))

    if cancel_event.is_set():
        return [], None, True

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    q.put(("status", {"stage": "transcribing",
                      "text": "Transcribing..."}))
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
        # Unconditioned, the prompt would reach only the first window.
        # openai-whisper 20250625 and later can repeat it in every one;
        # older versions have no such option and keep the old reach.
        if (not kwargs["condition_on_previous_text"]
                and _accepts_option(whisper.transcribe,
                                    "carry_initial_prompt")):
            kwargs["carry_initial_prompt"] = True

    audio_duration = params.get("audio_duration")
    writer = _ProgressWriter(q, audio_duration, t0,
                             captured=captured_segments)

    original_update = tqdm.tqdm.update

    def cancelling_update(self_, n=1):
        if cancel_event.is_set():
            raise _CancelledByUser()
        return original_update(self_, n)

    audio_input = params.get("_audio")
    if audio_input is None:
        audio_input = params["input"]

    tqdm.tqdm.update = cancelling_update
    try:
        with contextlib.redirect_stdout(writer), \
             contextlib.redirect_stderr(writer):
            try:
                result = model.transcribe(audio_input, **kwargs)
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


def _run_faster_whisper(params, q, cancel_event, on_segment=None):
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
    q.put(("status", {"stage": "loading",
                      "text": f"Loading model '{params['model']}'..."}))
    t0 = time.time()
    # device="auto" picks cuda if available, else cpu.
    # compute_type="auto" picks float16 on cuda, int8 on cpu - both fast,
    # both essentially indistinguishable from float32 for our use.
    # The download monitor surfaces the huggingface_hub fetch on first use.
    with _DownloadMonitor(
            cancel_event, params["model"],
            on_progress=lambda d: q.put(("download", d)),
            on_log=lambda t: q.put(("log", t))):
        model = WhisperModel(
            params["model"], device="auto", compute_type="auto")
    q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))

    if cancel_event.is_set():
        return [], None, True

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    q.put(("status", {"stage": "transcribing",
                      "text": "Transcribing..."}))
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
            "condition_on_previous_text", False),
        word_timestamps=params.get("word_timestamps", False),
    )
    # faster-whisper defaults to a beam of 5 when none is given, so a
    # beam of 1 (greedy decoding) must be passed explicitly - unlike the
    # reference engine above, where leaving it out is what means greedy.
    if params.get("beam_size"):
        kwargs["beam_size"] = int(params["beam_size"])
    if params.get("best_of"):
        kwargs["best_of"] = params["best_of"]
    if params.get("initial_prompt"):
        # Unconditioned, an initial_prompt reaches only the first 30
        # seconds: every later window starts from a clean slate. Sent as
        # hotwords it is placed in the same spot in every window's
        # prompt, so names and terms help throughout the recording.
        if kwargs["condition_on_previous_text"]:
            kwargs["initial_prompt"] = params["initial_prompt"]
        else:
            kwargs["hotwords"] = params["initial_prompt"]

    audio_duration = params.get("audio_duration")
    captured = []
    seg_dicts = []
    used_partial = False

    audio_input = params.get("_audio")
    if audio_input is None:
        audio_input = params["input"]
    segments_iter, info = model.transcribe(audio_input, **kwargs)
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
        # Hand it over now rather than at the end - this is what the
        # review pane fills in from while the run is still going.
        if on_segment is not None:
            on_segment((start, end, text),
                       _extract_word_conf({"segments": [seg_dicts[-1]]}))
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


def _run_mlx_whisper(params, q, cancel_event, on_segment=None):
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
            "condition_on_previous_text", False),
        word_timestamps=params.get("word_timestamps", False),
        # verbose=True so mlx prints '[mm:ss --> mm:ss] text' segment lines
        # as it decodes; _ProgressWriter parses them to drive the progress
        # bar (mlx exposes no per-segment callback otherwise).
        verbose=True,
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
        if not kwargs["condition_on_previous_text"]:
            q.put(("log",
                   "Note: mlx-whisper applies the context / vocabulary "
                   "hint to the first 30 seconds only when \"Condition "
                   "on previous text\" is off.\n"))

    audio_input = params.get("_audio")
    if audio_input is None:
        audio_input = params["input"]

    q.put(("log", f"Transcribing {Path(params['input']).name}...\n"))
    q.put(("status", {"stage": "loading",
                      "text": f"Loading model '{params['model']}'..."}))
    t0 = time.time()
    audio_duration = params.get("audio_duration")
    writer = _ProgressWriter(q, audio_duration, t0)
    # mlx downloads the model inside transcribe() on first use, so the
    # download monitor wraps the whole call. Only stdout is redirected -
    # the huggingface_hub download bar lives on stderr and is reported via
    # the monitor instead of spamming the log with carriage returns.
    with _DownloadMonitor(
            cancel_event, params["model"],
            on_progress=lambda d: q.put(("download", d)),
            on_log=lambda t: q.put(("log", t))):
        with contextlib.redirect_stdout(writer):
            result = mlx_whisper.transcribe(audio_input, **kwargs)
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
