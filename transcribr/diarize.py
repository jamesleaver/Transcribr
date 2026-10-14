"""Speaker detection: who is speaking in each paragraph."""

from pathlib import Path
import os
import shutil
import time

from .version import __version__
from .errors import DiarizationUnavailable, _CancelledByUser
from .speakers import MAX_SPEAKERS
from .platformutil import _config_dir, _log
from .channels import separated_channel_turns


#
# An optional post-pass that works out who is speaking: sherpa-onnx
# runs a small segmentation model (pyannote segmentation-3.0) plus a
# speaker-embedding model (TitaNet-S by default) and clusters the
# voices. Both models are ONNX and total ~45 MB, downloaded once on
# first use; onnxruntime is already in the dependency tree via
# faster-whisper.
#
# EXPERIMENTAL, and deliberately decoupled from paragraphify: the
# paragraph boundaries always come from the programmatic heuristics
# alone, and diarization only SUGGESTS a speaker label for each
# finished paragraph (majority voice by time overlap). Any paragraph
# whose attribution is doubtful is left unlabelled for the reviewer
# rather than guessed.

_DIARIZE_SEGMENTATION = {
    "url": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-segmentation-models/"
            "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"),
    "sha256": ("24615ee884c897d9d2ba09bb4d30da6b"
               "b1b15e685065962db5b02e76e4996488"),
    "archive_member": "model.onnx",
    "target": "pyannote-segmentation-3.0.onnx",
    "label": "speaker segmentation model (~7 MB)",
}

# The selectable voice-matching (speaker embedding) models. All run
# through the identical sherpa-onnx pipeline; they differ in how they
# "hear" voices, so when one merges two similar speakers another may
# separate them. Each downloads once, on first use.
DIARIZE_VOICE_MODELS = [
    # TitaNet is the default: it is the smaller and quicker of the two,
    # and the more reliable at keeping speakers apart.
    {"id": "titanet",
     "label": "TitaNet-S (NVIDIA)",
     "note": "The default — accurate, and the fastest.",
     "size": "~38 MB",
     "url": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/nemo_en_titanet_small.onnx"),
     "sha256": ("ad4a1802485d8b34c722d2a9d0424966"
                "2f2ece5d28a7a039063ca22f515a789e"),
     "archive_member": None,
     "target": "titanet-small.onnx"},
    {"id": "wespeaker",
     "label": "WeSpeaker ResNet34",
     "note": "A second opinion — worth a run when the default keeps "
             "giving two people one label.",
     "size": "~27 MB",
     "url": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/"      # (sic - upstream tag)
             "wespeaker_en_voxceleb_resnet34_LM.onnx"),
     "sha256": ("e9848563da86f263117134dfd7ad63c9"
                "2355b37de492b55e325400c9d9c39012"),
     "archive_member": None,
     "target": "wespeaker-voxceleb-resnet34-LM.onnx"},
]

DIARIZE_DEFAULT_VOICE_MODEL = "titanet"

# Voice models once offered and since withdrawn. Their weights are
# removed from the model cache the next time speaker detection runs,
# since nothing else would ever reclaim them - the Models page does not
# list diarization models. CAM++ was withdrawn as the least reliable of
# the voice models.
_RETIRED_VOICE_MODEL_FILES = ("campplus-voxceleb.onnx",)


def _voice_model_spec(voice_id):
    for spec in DIARIZE_VOICE_MODELS:
        if spec["id"] == voice_id:
            return spec
    return next(spec for spec in DIARIZE_VOICE_MODELS
                if spec["id"] == DIARIZE_DEFAULT_VOICE_MODEL)

# A paragraph is labelled only when its majority voice wins clearly
# among the time the diarizer attributed (MIN_MAJORITY of it), AND the
# diarizer attributed a meaningful share of the paragraph's span at all
# (MIN_COVERAGE - deliberately low, because engines pad segment spans
# with silence). Wrong labels are worse than missing ones.
_DIARIZE_MIN_MAJORITY = 0.6
_DIARIZE_MIN_COVERAGE = 0.3
# Clustering distance threshold when the speaker count is unknown.
# Higher merges more readily. sherpa-onnx documents 0.5, but on a
# four-person meeting that produced 67 to 73 clusters; at 0.9 - the most
# _run_diarization allows - TitaNet still found 23. Auto mode over-counts at any
# setting here, which is why the app now asks for the number instead.
_DIARIZE_CLUSTER_THRESHOLD = 0.9


def _diarize_models_dir():
    return _config_dir() / "models" / "diarization"


def diarize_models_ready(voice_id=DIARIZE_DEFAULT_VOICE_MODEL):
    d = _diarize_models_dir()
    return ((d / _DIARIZE_SEGMENTATION["target"]).exists()
            and (d / _voice_model_spec(voice_id)["target"]).exists())


def _download_one_model(spec, q, cancel_event):
    """Fetch, verify (sha256) and install one model file atomically.
    Progress goes to the run log."""
    import hashlib
    import tarfile
    import tempfile
    import urllib.request

    target = _diarize_models_dir() / spec["target"]
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    q.put(("log", f"Downloading {spec['label']}...\n"))

    with tempfile.TemporaryDirectory(dir=str(target.parent)) as tmpdir:
        fetched = Path(tmpdir) / "download"
        digest = hashlib.sha256()
        last_decile = -1
        req = urllib.request.Request(
            spec["url"], headers={"User-Agent": f"Transcribr/{__version__}"})
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(fetched, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise _CancelledByUser()
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    decile = int(done * 10 / total)
                    if decile > last_decile:
                        last_decile = decile
                        q.put(("log", f"  ... {min(100, decile * 10)}%\n"))

        if digest.hexdigest() != spec["sha256"]:
            raise DiarizationUnavailable(
                f"The downloaded {spec['label']} failed its integrity "
                "check. Please try again; if it keeps failing, the "
                "upstream file may have changed.")

        if spec["archive_member"]:
            with tarfile.open(fetched, "r:bz2") as tar:
                member = next(
                    (m for m in tar.getmembers()
                     if m.isfile() and Path(m.name).name
                     == spec["archive_member"]),
                    None)
                if member is None:
                    raise DiarizationUnavailable(
                        f"{spec['archive_member']} missing from the "
                        "downloaded archive.")
                src = tar.extractfile(member)
                extracted = Path(tmpdir) / "extracted"
                with open(extracted, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.replace(extracted, target)
        else:
            os.replace(fetched, target)


def _remove_retired_models():
    """Delete the weights of withdrawn voice models. Best effort: a file
    that cannot be removed is left for next time, never an error."""
    d = _diarize_models_dir()
    for name in _RETIRED_VOICE_MODEL_FILES:
        try:
            (d / name).unlink()
        except OSError:
            pass


def ensure_diarize_models(q, cancel_event=None,
                          voice_id=DIARIZE_DEFAULT_VOICE_MODEL):
    """Download any missing diarization models for `voice_id`. Raises
    DiarizationUnavailable (network trouble etc.) or _CancelledByUser."""
    import urllib.error
    voice = _voice_model_spec(voice_id)
    for spec in (_DIARIZE_SEGMENTATION,
                 {**voice,
                  "label": f"voice-matching model "
                           f"'{voice['label']}' ({voice['size']})"}):
        try:
            _download_one_model(spec, q, cancel_event)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise DiarizationUnavailable(
                f"Could not download the {spec['label']}:\n  {e}\n"
                "Check your internet connection and run again - the "
                "download is only needed once.")


def _run_diarization(audio, num_speakers, q, cancel_event=None,
                     voice_id=DIARIZE_DEFAULT_VOICE_MODEL,
                     threshold=None):
    """Run sherpa-onnx speaker diarization over mono 16 kHz float32
    samples. Returns [(start_seconds, end_seconds, speaker_int)] sorted
    by start time. `num_speakers` <= 0 means detect automatically using
    `threshold` (smaller = readier to hear two similar voices as
    different people)."""
    # Here rather than in ensure_diarize_models, which only runs when a
    # model is missing and so would never reach someone who already has
    # the default downloaded.
    _remove_retired_models()
    try:
        import sherpa_onnx
    except ImportError:
        raise DiarizationUnavailable(
            "Speaker detection needs the 'sherpa-onnx' package, which "
            "is not installed in this Python.\nInstall it with:\n"
            "  pip install sherpa-onnx")

    if threshold is None:
        threshold = _DIARIZE_CLUSTER_THRESHOLD
    threshold = min(0.9, max(0.2, float(threshold)))
    d = _diarize_models_dir()
    voice = _voice_model_spec(voice_id)
    q.put(("log", f"  voice-matching model: {voice['label']}\n"))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(d / _DIARIZE_SEGMENTATION["target"])),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(d / voice["target"])),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=(int(num_speakers) if num_speakers
                          and num_speakers > 0 else -1),
            threshold=threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)

    last_decile = [-1]

    def progress(num_processed_chunks, num_total_chunks):
        if num_total_chunks:
            pct = min(100, int(num_processed_chunks * 100
                               / num_total_chunks))
            q.put(("progress", {
                "pct": pct,
                "status_text": f"Identifying speakers… {pct}%",
                "stage": "diarizing",
            }))
            decile = pct // 10
            if decile > last_decile[0]:
                last_decile[0] = decile
                q.put(("log",
                       f"  ... listening for voices {decile * 10}%\n"))
        return 0

    t0 = time.time()
    result = sd.process(audio, callback=progress).sort_by_start_time()
    turns = [(float(r.start), float(r.end), int(r.speaker))
             for r in result]
    n = len({spk for _, _, spk in turns})
    q.put(("log",
           f"  found {n} speaker(s) in {time.time() - t0:.1f}s\n"))
    return turns


def speaker_turns(params, audio, q, cancel_event=None):
    """Who spoke when, by the most reliable means the recording allows.

    When the speakers are on separate channels or tracks - court
    systems, call recorders and conferencing exports often do this -
    the channel carrying each moment of speech says who it is, and that
    beats any voiceprint: no merged voices, no invented speakers, no
    model to download, and the speaker count is simply the channels in
    use. Otherwise it falls back to voiceprint diarization over the
    mono `audio`. Returns [(start, end, speaker)] turns; raises
    DiarizationUnavailable or _CancelledByUser."""
    if params.get("_channel_turns") is not None:
        # Already worked out for this run, before the paragraphs were.
        found = (params["_channel_turns"], params["_channel_verdict"])
    elif "_channel_turns" in params:
        found = None                # looked, and they are not separated
    else:
        try:
            found = separated_channel_turns(params["input"], cancel_event)
        except Exception:
            _log("channel analysis failed; using voiceprints")
            found = None
    if cancel_event is not None and cancel_event.is_set():
        raise _CancelledByUser()
    if found is not None:
        turns, verdict = found
        q.put(("log",
               f"\nSpeakers are on separate channels "
               f"({len(verdict['used'])} of {verdict['channels']} in use) - "
               "labelling each paragraph by channel rather than by voice.\n"))
        return turns

    if audio is None:
        raise DiarizationUnavailable(
            "the audio could not be decoded for speaker detection "
            "(the 'av' package is required).")
    voice_id = params.get("diarize_model") or DIARIZE_DEFAULT_VOICE_MODEL
    if not diarize_models_ready(voice_id):
        ensure_diarize_models(q, cancel_event, voice_id)
    q.put(("log", "\nIdentifying speakers (experimental)...\n"))
    return _run_diarization(
        audio, params.get("num_speakers") or 0, q, cancel_event,
        voice_id=voice_id, threshold=params.get("diarize_threshold"))


# ---- Suggesting labels for finished paragraphs ------------------------

def label_paragraphs(paragraphs, turns):
    """Suggest a review-slot letter ("1".."9" or None) for each
    already-formed paragraph from the diarized turns - the paragraphs
    themselves are never reshaped.

    Per paragraph, each speaker is credited with the time their turns
    overlap the paragraph's spoken segments. The majority speaker gets
    the label only when it clearly wins among the attributed time
    (_DIARIZE_MIN_MAJORITY) and the diarizer attributed a meaningful
    share of the paragraph at all (_DIARIZE_MIN_COVERAGE); anything
    doubtful stays unlabelled for the reviewer. Surviving speakers are
    ranked by credited time, the busiest nine keep slots, and
    numbering follows order of first appearance - the first voice
    heard becomes Speaker 1."""
    eligible = []       # per paragraph: speaker id or None
    for para in paragraphs:
        times, span = {}, 0.0
        for seg in para:
            s_start, s_end = seg[0], seg[1]
            span += max(0.0, s_end - s_start)
            for t_start, t_end, spk in turns:
                overlap = min(s_end, t_end) - max(s_start, t_start)
                if overlap > 0:
                    times[spk] = times.get(spk, 0.0) + overlap
        attributed = sum(times.values())
        if not times or span <= 0 \
                or attributed / span < _DIARIZE_MIN_COVERAGE:
            eligible.append(None)
            continue
        best = max(times, key=times.get)
        if times[best] / attributed < _DIARIZE_MIN_MAJORITY:
            eligible.append(None)
            continue
        eligible.append(best)

    totals = {}
    for spk, para in zip(eligible, paragraphs):
        if spk is None:
            continue
        totals[spk] = (totals.get(spk, 0.0)
                       + max(0.0, para[-1][1] - para[0][0]))
    keep = set(sorted(totals, key=totals.get, reverse=True)
               [:MAX_SPEAKERS])
    letters = {}
    for spk in (s for s in eligible if s is not None and s in keep):
        if spk not in letters:
            letters[spk] = str(len(letters) + 1)

    return [letters.get(spk) if spk is not None else None
            for spk in eligible]
