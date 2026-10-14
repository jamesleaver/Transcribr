"""Preparing source audio for playback, and re-transcribing a section."""

from pathlib import Path
import os
import queue
import shutil
import subprocess
import threading
import traceback

from .errors import ApiFail, _CancelledByUser, _EngineNotAvailable
from .platformutil import _config_dir, _log
from .paragraphs import (
    AUDIO_SAMPLE_RATE, _format_duration, decode_audio_16k,
    get_audio_duration, paragraphify
)
from .worker import (
    _run_faster_whisper, _run_mlx_whisper, _run_openai_whisper,
    detect_hallucinations
)
from .transcript import TranscriptModel, build_worker_params


#
# The review pane's <audio> element needs a source both webviews can
# actually play and seek: mp3/m4a/aac/wav are served as-is; anything
# else (video containers, flac/ogg/opus) is extracted once to AAC in
# an .m4a, cached under the config dir keyed by (path, size, mtime).
# When the source's audio stream is already AAC the extraction is a
# near-instant remux (-c:a copy). WKWebView refuses media from servers
# without Range support, which is why /audio/current is served through
# bottle's static_file.

# Served straight from the source: these containers record their exact
# duration, so the browser can map a timestamp to a byte offset.
_AUDIO_PASSTHROUGH_EXTS = {".m4a", ".wav"}

# .mp3 is deliberately NOT passed through. An mp3 with no Xing/Info
# header - routine from voice recorders, and from any tool that trims
# without re-encoding - carries no duration, so players estimate one
# from the first frame's bitrate. On a variable-bitrate file that
# estimate can be several times the truth, and every seek then lands at
# `wanted / estimated * filesize`, i.e. somewhere near the start. The
# review pane looked like it ignored the selected paragraph and always
# played from the beginning. Remuxing rebuilds the header; it copies
# packets, so there is no re-encode and no quality loss.
# Raw .aac (ADTS) has the same problem, worse - it falls through to the
# .m4a extraction below, which remuxes it into a proper mp4 container.
_AUDIO_REMUX_EXTS = {".mp3"}

# Content types for the formats /audio/current can end up serving. The
# extracted cache is .m4a, the last-resort fallback is .wav, and
# passthrough sources add the rest.
_AUDIO_MIMETYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
}
_AUDIO_CACHE_MAX_FILES = 8
_AUDIO_CACHE_MAX_BYTES = 1_000_000_000


def _audio_cache_dir():
    d = _config_dir() / "audio_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep_audio_cache():
    """Keep the newest few extracts within the size budget."""
    try:
        files = sorted([*_audio_cache_dir().glob("*.m4a"),
                        *_audio_cache_dir().glob("*.wav")],
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
    try:
        import av
        with av.open(str(path)) as container:
            if container.streams.audio:
                return container.streams.audio[0].codec_context.name
            return None
    except ImportError:
        pass
    except Exception:
        return None
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


def _remux_audio_pyav(src, target):
    """Copy `src`'s first audio stream into `target` without re-encoding,
    keeping the same codec and container. The point is the header the
    muxer writes on the way out: an mp3 that arrived with no Xing/Info
    frame gets one, so players know the real duration instead of
    extrapolating it from the first frame's bitrate - which is what made
    every seek land near the start of the file.

    Raises on any failure; the caller falls back to full extraction."""
    import av

    fmt = Path(target).suffix.lstrip(".").lower()
    with av.open(str(src)) as in_container:
        if not in_container.streams.audio:
            raise ValueError("No audio stream in file.")
        in_stream = in_container.streams.audio[0]
        with av.open(str(target), mode="w", format=fmt) as out_container:
            if hasattr(out_container, "add_stream_from_template"):
                out_stream = out_container.add_stream_from_template(in_stream)
            else:
                out_stream = out_container.add_stream(template=in_stream)
            packets = 0
            for packet in in_container.demux(in_stream):
                if packet.dts is None:
                    continue
                packet.stream = out_stream
                out_container.mux(packet)
                packets += 1
    if not packets:
        raise ValueError("No audio packets could be read.")


def _extract_audio_m4a_pyav(src, target):
    """Extract the first audio stream of `src` into an AAC .m4a at
    `target` using PyAV. Remuxes without re-encoding when the source
    audio is already AAC. Raises on any failure (including PyAV being
    absent) - the caller falls back to the ffmpeg binary."""
    import av

    with av.open(str(src)) as in_container:
        if not in_container.streams.audio:
            raise ValueError("No audio stream in file.")
        in_stream = in_container.streams.audio[0]
        codec = in_stream.codec_context.name

        with av.open(str(target), mode="w", format="mp4") as out_container:
            if codec == "aac":
                # Remux: copy packets straight across. PyAV renamed this
                # call - add_stream(template=...) was dropped in PyAV 12
                # in favour of add_stream_from_template(). Without both
                # spellings the remux raises on modern PyAV and every
                # video file silently falls through to the much larger,
                # 16 kHz WAV fallback.
                if hasattr(out_container, "add_stream_from_template"):
                    out_stream = out_container.add_stream_from_template(
                        in_stream)
                else:
                    out_stream = out_container.add_stream(
                        template=in_stream)
                for packet in in_container.demux(in_stream):
                    if packet.dts is None:
                        continue
                    packet.stream = out_stream
                    out_container.mux(packet)
                return

            # Transcode to AAC at the source rate; collapse >2 channels
            # to stereo (review playback doesn't need surround). The AAC
            # encoder demands fixed-size frames (1024 samples), but
            # decoders emit whatever the SOURCE codec used (WMA and
            # friends use big, varying frames) - so resampled audio is
            # staged through an AudioFifo and pulled out in exactly the
            # chunks the encoder accepts.
            rate = in_stream.codec_context.sample_rate or 44100
            channels = getattr(in_stream.codec_context, "channels", None)
            if channels is None:
                layout = getattr(in_stream.codec_context, "layout", None)
                channels = getattr(layout, "nb_channels", 2)
            layout = "mono" if channels == 1 else "stereo"
            out_stream = out_container.add_stream("aac", rate=rate)
            out_stream.bit_rate = 128_000
            resampler = av.AudioResampler(
                format="fltp", layout=layout, rate=rate)
            fifo = av.AudioFifo()
            chunk = out_stream.codec_context.frame_size or 1024

            def drain(final):
                while True:
                    got = fifo.read(chunk, partial=final)
                    if got is None or not got.samples:
                        return
                    got.pts = None
                    for pkt in out_stream.encode(got):
                        out_container.mux(pkt)

            def push(frame):
                for rf in resampler.resample(frame):
                    rf.pts = None
                    fifo.write(rf)
                drain(False)

            for frame in in_container.decode(in_stream):
                push(frame)
            push(None)                               # flush resampler
            drain(True)                              # flush fifo tail
            for pkt in out_stream.encode(None):      # flush encoder
                out_container.mux(pkt)


def retranscribe_slice(audio_path, start, end, settings, q, cancel_event):
    """Transcribe just [start, end) seconds of `audio_path` with the
    given settings dict (already carrying any model / conditioning
    overrides). The slice is written to a temporary 16 kHz wav so every
    engine handles it identically. Returns segment tuples offset back
    to absolute times in the source recording."""
    audio = decode_audio_16k(audio_path)
    if audio is None:
        raise RuntimeError(
            "The source audio could not be decoded for re-transcription "
            "(the 'av' package is required).")
    lo = max(0, int(float(start) * AUDIO_SAMPLE_RATE))
    hi = (len(audio) if end is None
          else max(lo, min(len(audio), int(float(end) * AUDIO_SAMPLE_RATE))))
    piece = audio[lo:hi]
    if len(piece) < AUDIO_SAMPLE_RATE // 2:
        raise RuntimeError("That section is too short to re-transcribe.")
    import tempfile
    import wave
    import numpy as np
    fd, tmp = tempfile.mkstemp(prefix="transcribr-retrans-",
                               suffix=".wav")
    os.close(fd)
    try:
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(AUDIO_SAMPLE_RATE)
            ints = np.clip(np.asarray(piece, dtype="float32") * 32767.0,
                           -32768, 32767).astype("<i2")
            w.writeframes(ints.tobytes())
        params = build_worker_params(settings, tmp, tmp + ".out.txt",
                                     review_before_save=False)
        params["_audio"] = piece
        from .worker import runner_for
        runner = runner_for(params, q, default="faster")
        segments, _result, _partial = runner(params, q, cancel_event)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    off = lo / float(AUDIO_SAMPLE_RATE)
    return [(float(seg[0]) + off, float(seg[1]) + off, seg[2])
            for seg in segments]


class RetransJob:
    """One selection re-transcription on a background thread, reporting
    "retrans" SSE events: running (carrying a progress percentage and
    the engine's live log lines) -> done | failed | cancelled. The
    result is applied to the session only if the client's revision is
    still current, so edits made meanwhile are never clobbered."""

    def __init__(self, backend, session, settings, lo, hi):
        self.backend = backend
        self.session = session
        self.settings = settings
        self.lo = lo
        self.hi = hi
        self.state = "running"
        self.cancel_event = threading.Event()
        self.q = queue.Queue()          # engine log/progress messages
        self._done = threading.Event()  # tells the drain thread to stop
        # The whole selection is one span: [start of the first selected
        # paragraph, start of the paragraph just past the selection),
        # or to end-of-file when it reaches the last paragraph.
        m = session.model
        start = float(m.paragraphs[lo][0][0])
        end = (float(m.paragraphs[hi + 1][0][0])
               if hi + 1 < len(m.paragraphs) else None)
        self.span = (start, end)
        # Lock the target paragraphs by identity: they grey out and
        # can't be edited, but the rest of the transcript stays live,
        # and the result splices back onto these ids wherever they end
        # up after edits elsewhere.
        self.locked_ids = session.lock_paragraphs(lo, hi)
        threading.Thread(target=self._run, daemon=True,
                         name="transcribr-retrans").start()

    def _publish(self, state, message="", **extra):
        self.state = state
        self.backend.broker.publish(
            "retrans", {"state": state, "message": message, **extra})

    def _overall_pct(self, frac):
        """One slice's 0..1 fraction as a percentage."""
        return max(0.0, min(100.0, min(1.0, frac) * 100))

    def _drain(self):
        """Forward the engine's queue to the client as it runs: log
        lines fill the output box, 'eta' messages drive the progress
        bar, and stage markers ('status'/'download') caption it."""
        while True:
            try:
                first = self.q.get(timeout=0.2)
            except queue.Empty:
                if self._done.is_set():
                    return
                continue
            batch = [first]
            while True:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            log_delta = ""
            prog = None
            for kind, data in batch:
                if kind == "log":
                    log_delta += data
                elif kind == "eta" and data.get("audio_total"):
                    frac = data["audio_done"] / data["audio_total"]
                    prog = {
                        "pct": self._overall_pct(frac),
                        "indeterminate": False,
                        "status_text": (
                            f"{_format_duration(data['audio_done'])} of "
                            f"{_format_duration(data['audio_total'])}"
                            f"   ·   {data['speed']:.1f}x speed"),
                    }
                elif kind == "status":
                    stage = data.get("stage", "")
                    prog = {
                        "pct": self._overall_pct(0.0),
                        "indeterminate": True,
                        "status_text": {
                            "loading": "Loading model…",
                            "transcribing": "Transcribing…",
                        }.get(stage, "Working…"),
                    }
                elif kind == "download":
                    total = data.get("total") or 0
                    got = data.get("downloaded") or 0
                    prog = {
                        "pct": self._overall_pct(got / total if total else 0),
                        "indeterminate": not total,
                        "status_text": (
                            f"Downloading model "
                            f"'{data.get('model', '')}' (first use)"),
                    }
            if log_delta or prog:
                ev = {"state": "running", "message": ""}
                if log_delta:
                    ev["log_delta"] = log_delta
                if prog:
                    ev.update(prog)
                self.backend.broker.publish("retrans", ev)

    def _run(self):
        drain = threading.Thread(target=self._drain, daemon=True,
                                 name="transcribr-retrans-log")
        drain.start()
        try:
            self._publish("running", "Re-transcribing the selection…",
                          pct=0.0, indeterminate=True)
            start, end = self.span
            segments = retranscribe_slice(
                self.session.audio_path, start, end,
                self.settings, self.q, self.cancel_event)
            self.session.apply_retranscribe(
                self.locked_ids, segments, self.settings["gap"])
            self._publish("done",
                          "Done - the selection was replaced (undo "
                          "reverses it).", pct=100.0)
        except _CancelledByUser:
            self._publish("cancelled", "Re-transcription stopped.")
        except _EngineNotAvailable as e:
            self._publish("failed", str(e))
        except ApiFail as e:
            self._publish("failed", str(e))
        except Exception as e:
            _log("retranscribe failed:\n" + traceback.format_exc())
            self._publish("failed", f"{type(e).__name__}: {e}")
        finally:
            # apply_retranscribe clears the lock on success; make sure a
            # failed/cancelled run un-greys the section too.
            self.session.clear_lock()
            self._done.set()


def flagged_spans(model, join_gap=1, max_spans=12):
    """Contiguous runs of paragraphs that detect_hallucinations() flagged,
    as (lo, hi) index pairs. Runs separated by fewer than `join_gap`
    clean paragraphs are merged, because re-running one longer stretch
    gives the engine more context than two short ones and costs less
    than two model loads."""
    conf = None
    if model.word_conf:
        conf = [[p for _w, p in bucket]
                for bucket in model._bucket_words_by_paragraph()]
    flagged = detect_hallucinations(model.paragraphs, confidences=conf)
    if not flagged:
        return []
    spans = [[flagged[0], flagged[0]]]
    for idx in flagged[1:]:
        if idx - spans[-1][1] <= join_gap + 1:
            spans[-1][1] = idx
        else:
            spans.append([idx, idx])
    return [(lo, hi) for lo, hi in spans[:max_spans]]


MAX_RETRY_SPANS = 12
MAX_RETRY_FRACTION = 0.25


def _budgeted_spans(spans, paragraphs):
    """Trim the work list so a badly-behaved recording cannot double the
    time to a usable transcript. A transcript that is mostly flagged is
    a bad recording, not something a second pass will rescue."""
    if not paragraphs:
        return []
    total = float(paragraphs[-1][-1][1])
    if total <= 0:
        return spans[:MAX_RETRY_SPANS]
    budget, kept = total * MAX_RETRY_FRACTION, []
    for lo, hi in spans[:MAX_RETRY_SPANS]:
        length = float(paragraphs[hi][-1][1]) - float(paragraphs[lo][0][0])
        if budget - length < 0:
            break
        budget -= length
        kept.append((lo, hi))
    return kept


# The model the first repair attempt uses. Small on purpose: the first
# attempt runs while the main engine is still loaded, and a second copy
# of large-v3-turbo would roughly double peak memory - enough to get the
# app killed on an 8 GB machine, which is precisely the failure the
# child-process work exists to survive. Runaway repetition and dead-air
# phantoms are gross errors that a small model resolves perfectly well;
# anything it cannot fix is escalated to the full model afterwards,
# when the main engine has let go of its memory.
REPAIR_FIRST_MODEL = "small.en"


def repair_settings(settings, *, tier):
    """Engine settings for a repair attempt.

    Conditioning is off for both tiers: feeding each chunk the text
    before it is what turns one bad guess into a runaway repetition, so
    it earns its place on the first pass and is exactly what to drop on
    a retry. Tier 1 also drops to a small model; tier 2 keeps whatever
    the run itself used."""
    out = dict(settings)
    out["condition_on_previous_text"] = False
    if tier == 1:
        out["model"] = REPAIR_FIRST_MODEL
        # small.en is English-only, so a run in another language has
        # nothing to gain from it and goes straight to the full model.
        if str(out.get("language") or "").strip() not in ("English", ""):
            return None
    return out


def _leaves_it_better(paragraphs, lo, hi, candidate):
    """Whether replacing paragraphs [lo..hi] with `candidate` leaves
    fewer of them looking like engine error than before.

    Judged with the neighbours the replacement will actually sit
    between, because one of the things detect_hallucinations looks for
    is a paragraph that repeats the one next to it - which a candidate
    examined on its own cannot possibly show. Testing it in isolation
    let the pass replace one flagged paragraph with a clean-looking
    copy of the paragraph above it, and come away with two flagged
    paragraphs where there had been one: the same sentence twice, and
    the section it was asked to fix made worse by its own measure.

    One paragraph of context each side is enough; that is as far as the
    adjacent-duplicate test reaches."""
    lo_ctx = max(0, lo - 1)
    hi_ctx = min(len(paragraphs), hi + 2)
    before = paragraphs[lo_ctx:hi_ctx]
    after = (paragraphs[lo_ctx:lo] + [list(p) for p in candidate]
             + paragraphs[hi + 1:hi_ctx])
    return len(detect_hallucinations(after)) < len(
        detect_hallucinations(before))


def auto_retranscribe(paragraphs, word_conf, audio_path, settings,
                      log=None, progress=None, cancel_event=None,
                      label="second pass, without conditioning"):
    """Transcribe the flagged sections a second time, with conditioning
    off, and keep whichever results the same check no longer objects to.

    Conditioning feeds each chunk the text before it, which is what
    turns one bad guess into a runaway repetition - so it earns its
    place on the first pass and is exactly what to drop on the retry.

    The acceptance test makes this safe by construction: a replacement
    is kept only when detect_hallucinations() is happy with it, so the
    pass can reduce the number of flagged paragraphs but never add one.

    Returns (paragraphs, word_conf, attempted, runs), where `runs` is
    the [(lo, hi, new_paragraphs)] it accepted, indexed against the
    paragraph list it was given. A caller applying those to a
    transcript somebody may have edited meanwhile should resolve the
    indices through paragraph ids rather than trusting them. Both lists
    are new; the caller's are untouched.
    """
    paragraphs = [list(p) for p in paragraphs]
    words = list(word_conf or [])
    settings = dict(settings)
    settings["condition_on_previous_text"] = False
    # The engine runners check this unconditionally, so it must be a
    # real Event even when the caller has nothing to cancel with.
    if cancel_event is None:
        cancel_event = threading.Event()

    spans = _budgeted_spans(
        flagged_spans(TranscriptModel(paragraphs, word_conf=words)),
        paragraphs)
    if not spans:
        return paragraphs, words, 0, []

    def say(text):
        if log:
            log(text)

    # Announced here rather than by the caller, because only this far
    # in is it known whether there is anything to check. Saying "now
    # re-checking the flagged sections" over a clean transcript, twice,
    # is just noise.
    say(f"\nRe-checking {len(spans)} flagged "
        f"{'section' if len(spans) == 1 else 'sections'} "
        f"({label})...\n")

    attempted = 0
    runs = []
    q = queue.Queue()
    # Last span first: replacing one shifts every index after it, and
    # working backwards leaves the earlier spans' indices valid.
    for n, (lo, hi) in enumerate(reversed(spans)):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress:
            progress(100.0 * n / len(spans),
                     f"Re-checking flagged section {n + 1} of {len(spans)}")
        start_t = float(paragraphs[lo][0][0])
        end_t = (float(paragraphs[hi + 1][0][0])
                 if hi + 1 < len(paragraphs) else None)
        attempted += 1
        try:
            segments = retranscribe_slice(audio_path, start_t, end_t,
                                          settings, q, cancel_event)
        except _CancelledByUser:
            break
        except Exception as e:
            _log("second pass over one section failed:\n"
                 + traceback.format_exc())
            say(f"  section at {_format_duration(start_t)}: "
                f"could not be re-run ({type(e).__name__}).\n")
            continue
        finally:
            while True:                     # keep the engine's own log
                try:
                    kind, data = q.get_nowait()
                except queue.Empty:
                    break
                if kind == "log" and log:
                    log(data)

        candidate = paragraphify(segments, settings["gap"])
        if not candidate or not _leaves_it_better(paragraphs, lo, hi,
                                                  candidate):
            say(f"  section at {_format_duration(start_t)}: "
                f"no better on a second pass, left as it was.\n")
            continue

        runs.append((lo, hi, candidate))
        paragraphs[lo:hi + 1] = candidate
        # The old word confidences describe text that is no longer here;
        # keeping them would shade the new words by the old ones' scores.
        limit = end_t if end_t is not None else float("inf")
        words = [w for w in words if not (start_t <= w[0] < limit)]
        say(f"  section at {_format_duration(start_t)}: replaced.\n")

    if runs:
        say(f"Second pass: {len(runs)} of {attempted} "
            f"{'section' if attempted == 1 else 'sections'} improved.\n")
    else:
        say(f"Second pass: none of the {attempted} flagged "
            f"{'section' if attempted == 1 else 'sections'} came back "
            f"cleaner, so nothing was changed.\n")
    # Ascending, which is the order a caller resolving them by id will
    # want; applying by index still has to go right to left.
    return paragraphs, words, attempted, sorted(runs)


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
        # Distinguishes this session's audio URL from earlier sessions'
        # so the browser can never reuse the previously loaded file.
        self.version = os.urandom(4).hex()
        if audio_path:
            threading.Thread(target=self._run, daemon=True,
                             name="transcribr-audio").start()

    def status(self):
        out = {"state": self.state}
        if self.state == "ready":
            out["url"] = f"/audio/current?v={self.version}"
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
            suffix = src.suffix.lower()
            if suffix in _AUDIO_PASSTHROUGH_EXTS:
                self.serve_path = str(src)
                self._set("ready")
                return
            key = _audio_cache_key(src)
            target = _audio_cache_dir() / f"{key}.m4a"
            wav_target = _audio_cache_dir() / f"{key}.wav"
            remux_target = _audio_cache_dir() / f"{key}{suffix}"
            for cached in (target, wav_target, remux_target):
                if cached.exists():
                    self.serve_path = str(cached)
                    self._set("ready")
                    return
            self._set("extracting")

            # Formats that only need their header rebuilt (see
            # _AUDIO_REMUX_EXTS): copy the packets across, keep the
            # codec. Falls through to the full extraction below if the
            # remux fails for any reason.
            if suffix in _AUDIO_REMUX_EXTS:
                rtmp = remux_target.with_suffix(f".part{suffix}")
                try:
                    _remux_audio_pyav(src, rtmp)
                    os.replace(rtmp, remux_target)
                    _sweep_audio_cache()
                    self.serve_path = str(remux_target)
                    # The source's estimate was the thing we distrusted;
                    # re-probe now that the header is right.
                    fixed = get_audio_duration(str(remux_target))
                    if fixed:
                        self.duration = fixed
                    self._set("ready")
                    return
                except Exception:
                    _log("Lossless remux failed, falling back to "
                         "extraction:\n" + traceback.format_exc())
                    rtmp.unlink(missing_ok=True)
            tmp = target.with_suffix(".part.m4a")

            def wav_fallback():
                """Last resort that covers anything the app can
                transcribe: the transcription decode path itself
                (16 kHz mono), written as a plain WAV. Bigger on disk
                than AAC but universally playable."""
                try:
                    samples = decode_audio_16k(str(src))
                    if samples is None or not len(samples):
                        return False
                    import wave
                    import numpy as np
                    wtmp = wav_target.with_suffix(".part.wav")
                    with wave.open(str(wtmp), "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(AUDIO_SAMPLE_RATE)
                        ints = np.clip(
                            np.asarray(samples, dtype="float32")
                            * 32767.0, -32768, 32767).astype("<i2")
                        w.writeframes(ints.tobytes())
                    os.replace(wtmp, wav_target)
                    _sweep_audio_cache()
                    self.serve_path = str(wav_target)
                    self._set("ready")
                    return True
                except Exception:
                    _log("WAV playback fallback failed:\n"
                         + traceback.format_exc())
                    return False

            # PyAV AAC extract first (small file, no external binary),
            # the ffmpeg binary next, then the WAV fallback - and a
            # clear message when everything fails.
            try:
                _extract_audio_m4a_pyav(src, tmp)
            except Exception as extract_err:
                _log("PyAV playback extraction failed, trying ffmpeg:\n"
                     + traceback.format_exc())
                tmp.unlink(missing_ok=True)
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    if wav_fallback():
                        return
                    # Say what actually went wrong: a broken install is
                    # fixed by reinstalling; an undecodable file needs
                    # converting or an ffmpeg install.
                    try:
                        # Probe, not an import for use: if PyAV is
                        # absent this raises and the message below
                        # says so instead of blaming the file.
                        import av  # noqa: F401
                        detail = str(extract_err).strip() or type(
                            extract_err).__name__
                        self._set(
                            "unavailable",
                            f"Couldn't read this file's audio "
                            f"({detail}). Its format may not be "
                            "supported - converting it to .mp3/.wav, "
                            "or installing ffmpeg, should fix "
                            "playback.")
                    except Exception:
                        self._set(
                            "unavailable",
                            "The app's audio component (PyAV) is "
                            "missing or broken - re-running the "
                            "Transcribr installer fixes this.")
                    return
                codec_args = (["-c:a", "copy"]
                              if _source_audio_codec(src) == "aac"
                              else ["-c:a", "aac", "-b:a", "128k"])
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                       "-y", "-i", str(src), "-vn", "-map", "0:a:0",
                       *codec_args, str(tmp)]
                proc = subprocess.run(cmd, capture_output=True,
                                      text=True, timeout=1800)
                if proc.returncode != 0 or not tmp.exists():
                    tmp.unlink(missing_ok=True)
                    if wav_fallback():
                        return
                    detail = (proc.stderr or "").strip().splitlines()
                    self._set("unavailable",
                              detail[-1] if detail
                              else "Extraction failed.")
                    return
            os.replace(tmp, target)
            _sweep_audio_cache()
            self.serve_path = str(target)
            self._set("ready")
        except Exception as e:
            _log(f"audio prep failed: {traceback.format_exc()}")
            self._set("unavailable", f"{type(e).__name__}: {e}")
