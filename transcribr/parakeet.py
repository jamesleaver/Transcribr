"""NVIDIA Parakeet TDT 0.6B v2: an English speech recogniser that is
not Whisper.

It is quick and does not fall into the repetition loops Whisper is
prone to. It runs on sherpa-onnx, which the app already
ships for speaker detection, so it adds a model download (~480 MB,
once, CC BY 4.0) and nothing else.

It is offered as a model, "parakeet-tdt-0.6b-v2", behind the "Quick
draft" tier for English. It knows English only; any other language
falls back to Whisper.

Long recordings are decoded in windows of at most 30 seconds, each cut
at its quietest moment so a cut rarely lands in a word. Every token
comes with its time, duration and log-probability, so the transcript
has real word timings and word confidence like Whisper's.
"""

import math
import os
import shutil
import time
from pathlib import Path

from .errors import _CancelledByUser
from .platformutil import _config_dir, _log
from .version import __version__

MODEL_ID = "parakeet-tdt-0.6b-v2"
LABEL = "Parakeet TDT 0.6B (NVIDIA)"
SAMPLE_RATE = 16000
WINDOW_S = 30.0
QUIET_SEARCH_S = 5.0

_SPEC = {
    "url": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2"),
    "sha256": ("157c157bc51155e03e37d2466522a3a7"
               "37dd9c72bb25f36eb18912964161e1ad"),
    "members": ("encoder.int8.onnx", "decoder.int8.onnx",
                "joiner.int8.onnx", "tokens.txt"),
    "size": "~480 MB",
}


class ParakeetUnavailable(RuntimeError):
    """The model cannot be used: sherpa-onnx missing, or the download
    failed. The message is written for the person running the app."""


def models_dir():
    return _config_dir() / "models" / "parakeet"


def model_ready():
    d = models_dir()
    return all((d / m).exists() for m in _SPEC["members"])


def model_size():
    """Bytes the installed model occupies, or 0."""
    d = models_dir()
    total = 0
    for m in _SPEC["members"]:
        try:
            total += (d / m).stat().st_size
        except OSError:
            return 0
    return total


def remove_model():
    """Delete the model. Returns bytes freed."""
    freed = model_size()
    for m in _SPEC["members"]:
        try:
            (models_dir() / m).unlink()
        except OSError:
            pass
    return freed


def ensure_model(q, cancel_event=None, on_progress=None):
    """Download, verify (sha256) and unpack the model on first use.
    `on_progress` receives {"downloaded", "total", "speed"} so the
    Models page can show a progress bar."""
    import hashlib
    import tarfile
    import tempfile
    import urllib.error
    import urllib.request

    if model_ready():
        return
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    q.put(("log", f"Downloading the Parakeet speech model "
                  f"({_SPEC['size']}, once)...\n"))
    q.put(("status", {"stage": "loading",
                      "text": "Downloading the Parakeet model..."}))
    try:
        with tempfile.TemporaryDirectory(dir=str(target)) as tmpdir:
            fetched = Path(tmpdir) / "download"
            digest = hashlib.sha256()
            req = urllib.request.Request(
                _SPEC["url"],
                headers={"User-Agent": f"Transcribr/{__version__}"})
            last_decile = -1
            started = time.time()
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(fetched, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _CancelledByUser()
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        elapsed = max(1e-6, time.time() - started)
                        on_progress({"downloaded": done, "total": total,
                                     "speed": done / elapsed})
                    if total:
                        decile = int(done * 10 / total)
                        if decile > last_decile:
                            last_decile = decile
                            q.put(("log",
                                   f"  ... {min(100, decile * 10)}%\n"))
            if digest.hexdigest() != _SPEC["sha256"]:
                raise ParakeetUnavailable(
                    "The downloaded Parakeet model failed its integrity "
                    "check. Please try again; if it keeps failing, the "
                    "upstream file may have changed.")
            with tarfile.open(fetched, "r:bz2") as tar:
                for name in _SPEC["members"]:
                    member = next((m for m in tar.getmembers()
                                   if m.isfile()
                                   and Path(m.name).name == name), None)
                    if member is None:
                        raise ParakeetUnavailable(
                            f"{name} is missing from the downloaded "
                            "Parakeet archive.")
                    part = Path(tmpdir) / name
                    with tar.extractfile(member) as src, \
                            open(part, "wb") as out:
                        shutil.copyfileobj(src, out)
            # tokens.txt last: its presence is what model_ready() trusts
            # alongside the others, and a half-installed model must
            # never look complete.
            for name in _SPEC["members"]:
                os.replace(Path(tmpdir) / name, target / name)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ParakeetUnavailable(
            f"Could not download the Parakeet model:\n  {e}\n"
            "Check your internet connection and run again - the download "
            "is only needed once.")


def quiet_windows(audio, max_s=WINDOW_S, search_s=QUIET_SEARCH_S,
                  sr=SAMPLE_RATE):
    """[(first sample, last sample)]: windows of at most `max_s`, each
    ending at the quietest 20 ms in its last `search_s`."""
    import numpy as np
    hop = sr // 50
    n = len(audio) // hop
    if n == 0:
        return [(0, len(audio))] if len(audio) else []
    energy = np.square(np.asarray(audio[:n * hop], dtype=np.float32)
                       .reshape(n, hop)).mean(axis=1)
    out, start = [], 0
    step, search = int(max_s * 50), int(search_s * 50)
    while start < n:
        if n - start <= step:
            end = n
        else:
            lo = start + step - search
            end = lo + int(np.argmin(energy[lo:start + step])) + 1
        out.append((start * hop, end * hop if end < n else len(audio)))
        start = end
    return out


def words_from(result, offset):
    """[(start, end, text, probability)] from one window's result.
    A token that begins with a space starts a new word; the word ends
    where its last token does; its probability is its least confident
    token's."""
    words = []
    durations = list(getattr(result, "durations", []) or [])
    logps = list(getattr(result, "ys_log_probs", []) or [])
    for i, (tok, t) in enumerate(zip(result.tokens, result.timestamps)):
        start = offset + float(t)
        dur = float(durations[i]) if i < len(durations) else 0.08
        end = start + max(dur, 0.04)
        p = math.exp(float(logps[i])) if i < len(logps) else None
        tok = tok.replace("▁", " ")
        if tok.startswith(" ") or not words:
            words.append([start, end, tok.strip(), p])
        else:
            w = words[-1]
            w[2] += tok
            w[1] = max(w[1], end)
            if p is not None:
                w[3] = p if w[3] is None else min(w[3], p)
    return [tuple(w) for w in words if w[2]]


def _sentence_segment(ws):
    text = " ".join(w[2] for w in ws)
    return ((ws[0][0], ws[-1][1], text),
            {"start": ws[0][0], "end": ws[-1][1], "text": text,
             "words": [{"start": w[0], "end": w[1], "word": " " + w[2],
                        "probability": w[3]} for w in ws]})


_recognizer = None


def _load(num_threads):
    global _recognizer
    if _recognizer is None:
        import sherpa_onnx
        d = models_dir()
        _recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"),
            tokens=str(d / "tokens.txt"), model_type="nemo_transducer",
            num_threads=num_threads)
    return _recognizer


def release():
    """Let the model's memory go (it holds ~1 GB while loaded)."""
    global _recognizer
    _recognizer = None


def run(params, q, cancel_event, on_segment=None):
    """Transcribe params["_audio"] (16 kHz mono float32). Returns
    (segments, result, used_partial) in the same shapes as the Whisper
    runners: one segment per sentence, with word timings."""
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError as e:
        raise ParakeetUnavailable(
            "Parakeet needs the sherpa-onnx package, which this "
            f"installation does not have ({e}).")
    audio = params.get("_audio")
    if audio is None:
        from .audioprep import decode_audio_16k
        audio = decode_audio_16k(params["input"])
    if audio is None:
        raise ParakeetUnavailable("The audio could not be decoded.")
    ensure_model(q, cancel_event)
    if params.get("initial_prompt"):
        q.put(("log", "Note: Parakeet does not use the context / "
                      "vocabulary hint.\n"))

    q.put(("log", f"Loading {LABEL}...\n"))
    q.put(("status", {"stage": "loading", "text": f"Loading {LABEL}..."}))
    t0 = time.time()
    rec = _load(max(1, min(4, (os.cpu_count() or 4) // 2)))
    q.put(("log", f"  loaded in {time.time() - t0:.1f}s\n\n"))
    q.put(("status", {"stage": "transcribing", "text": "Transcribing..."}))

    total = len(audio) / SAMPLE_RATE
    captured, seg_dicts, pending = [], [], []
    used_partial = False
    t0 = time.time()

    def emit(ws):
        seg, d = _sentence_segment(ws)
        captured.append(seg)
        seg_dicts.append(d)
        if on_segment is not None:
            on_segment(seg, [(w[0], w[1], " " + w[2], w[3]) for w in ws])
        q.put(("log", f"{seg[2]}\n"))

    for a, b in quiet_windows(audio):
        if cancel_event.is_set():
            used_partial = True
            q.put(("log", "\n[Stopped by user - saving what was "
                          "transcribed so far]\n"))
            break
        stream = rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio[a:b])
        rec.decode_stream(stream)
        for w in words_from(stream.result, a / SAMPLE_RATE):
            pending.append(w)
            if w[2].endswith((".", "?", "!")):
                emit(pending)
                pending = []
        del stream
        done = b / SAMPLE_RATE
        wall = time.time() - t0
        if wall > 0 and done >= 5.0:
            speed = done / wall
            q.put(("eta", {"audio_done": done, "audio_total": total,
                           "wall_elapsed": wall,
                           "eta_seconds": max(0.0, total - done) / speed,
                           "speed": speed}))
    if pending:
        emit(pending)
    if not used_partial:
        q.put(("log", f"\n  transcribed in {time.time() - t0:.1f}s\n\n"))
    _log(f"parakeet: {len(captured)} segments, {total:.0f}s audio")
    return captured, {"segments": seg_dicts, "language": "en"}, used_partial
