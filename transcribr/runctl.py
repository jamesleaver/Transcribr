"""The run/batch state machine behind the Transcribe page."""

from pathlib import Path
import os
import queue
import threading
import traceback

from .errors import ApiFail, _EngineNotAvailable
from .platformutil import _AUDIO_EXTENSIONS, _log, _recent_add
from .paragraphs import _format_duration
from .worker import _humanize_bytes
from .workerproc import default_worker_fn
from .transcript import build_worker_params


_NO_ENGINE_HINT = (
    "No Whisper engine is installed in this Python.\n\n"
    "Install at least one:\n"
    "  pip install openai-whisper\n"
    "  pip install faster-whisper\n"
    "  pip install mlx-whisper   (Apple Silicon only)")


# Extensions that mark a file as a recording. A transcript must never
# be written to a path that looks like one.
# The output-safety guard refuses to write over anything wearing a
# media extension; sharing the master list keeps it as broad as what
# the app can open.
_MEDIA_EXTENSIONS = set(_AUDIO_EXTENSIONS)


def ensure_output_is_safe(in_path, out_path):
    """Refuse output paths that could destroy a recording: the input
    itself (however it is spelled or linked), or any path wearing an
    audio/video extension. Raises ApiFail; there is deliberately no
    force/overwrite override for these.

    The failure it prevents: a save-dialog misclick sets Output to the
    source .mp3, the user confirms the generic "already exists -
    overwrite?" prompt, and the pre-review safety copy replaces the
    recording with a Word document."""
    src, dst = Path(in_path), Path(out_path)
    try:
        same = (os.path.normcase(str(src.resolve()))
                == os.path.normcase(str(dst.resolve())))
        if not same and dst.exists():
            same = dst.samefile(src)
    except OSError:
        same = False
    if same:
        raise ApiFail(
            400, "output_is_input",
            "The output path is the input recording itself - saving "
            "would destroy the recording.\n\nChoose a different output "
            "file (leave the field empty to save next to the input as "
            f"'{Path(in_path).stem}.transcript.<format>').")
    if dst.suffix.lower() in _MEDIA_EXTENSIONS:
        raise ApiFail(
            400, "output_looks_like_media",
            f"The output path ends in '{dst.suffix}', which is an "
            "audio/video extension - transcripts save as .txt, .docx "
            "or .pdf. This guard exists so a transcript can never "
            "overwrite a recording.\n\nCheck the Output field: it may "
            "be pointing at a recording by mistake.")


# The run's whole life. Named here rather than left as a comment
# because contract.py generates the front-end's RunPhase from it.
RUN_PHASES = ("idle", "running", "stopping", "done", "error", "cancelled")


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
        self.worker_fn = worker_fn or default_worker_fn()
        self.lock = threading.RLock()
        self._batch = None
        self.phase = RUN_PHASES[0]
        self.current_file = None
        self.run_id = 0
        self.last_output = None
        self.log_lines = []
        self.progress = None
        # Phase 3 sets this to route paragraphs_ready into a review
        # session; until then runs force review_before_save=False.
        self.on_paragraphs_ready = None
        # Set by WebBackend for a run that fills a review pane as it
        # goes. Left None, the run behaves exactly as it did before:
        # the pane opens once, at the end.
        self.on_transcript_started = None
        self.on_paragraphs_grew = None
        # Fired however the run ends. A session that filled in during
        # the run has to be sealed by something, and paragraphs_ready
        # is not guaranteed to arrive - a stopped or failed run may
        # never send it, which used to leave the transcript live, and
        # therefore unsaveable, for good.
        self.on_run_ended = None
        # Set by WebBackend to the model manager's is_busy(), so a run
        # can't start while a model download holds the weights cache.
        self.model_busy_check = None

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
        if self.model_busy_check is not None and self.model_busy_check():
            raise ApiFail(409, "model_busy",
                          "A model is downloading. Wait for it to finish "
                          "before starting a transcription.")

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
            ensure_output_is_safe(in_path, out_path)
            if Path(out_path).exists() and not force:
                raise ApiFail(409, "output_exists",
                              f"The output file already exists:\n\n"
                              f"{out_path}", path=out_path)
            try:
                params = build_worker_params(
                    settings, in_path, out_path,
                    # Single files always pause for review since 0.9.0
                    # (batches still save directly).
                    review_before_save=True)
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
                batch_out = str(Path(in_path).with_suffix(
                    f".transcript.{ext}"))
                ensure_output_is_safe(in_path, batch_out)
                items.append((in_path, batch_out))
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
            elif kind == "progress":
                # Direct phase progress (e.g. the speaker-detection
                # pass after transcription reaches 100%).
                self.progress = {
                    "pct": max(0.0, min(100.0,
                                        float(data.get("pct", 0)))),
                    "status_text": str(data.get("status_text", "")),
                    **({"stage": data["stage"]}
                       if data.get("stage") else {}),
                }
                self.broker.publish("progress", self.progress)
            elif kind == "download":
                self._handle_download(data)
            elif kind == "status":
                self._handle_status(data)
            elif kind == "transcript_started":
                if self.on_transcript_started is not None:
                    self.on_transcript_started(data)
            elif kind == "paragraphs_grew":
                if self.on_paragraphs_grew is not None:
                    self.on_paragraphs_grew(data)
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
            "stage": "transcribing",
            "status_text": (
                f"{_format_duration(done)} of {_format_duration(total)}"
                f"   ·   about {_format_duration(info['eta_seconds'])} "
                f"remaining   ·   {info['speed']:.1f}x audio speed"),
            **info,
        }
        self.broker.publish("progress", self.progress)

    def _handle_download(self, info):
        """Turn a worker 'download' message (first-run model fetch) into a
        moving progress bar with a clear 'Downloading model...' status, so
        a multi-gigabyte download never looks like a frozen app."""
        downloaded = info.get("downloaded") or 0
        total = info.get("total") or 0
        speed = info.get("speed") or 0
        pct = (downloaded / total * 100) if total else 0
        parts = [f"Downloading model '{info.get('model', '')}'",
                 f"{_humanize_bytes(downloaded)} of {_humanize_bytes(total)}"]
        if speed > 0:
            parts.append(f"{_humanize_bytes(speed)}/s")
        parts.append("first use only")
        self.progress = {
            "pct": max(0.0, min(100.0, float(pct))),
            "stage": "downloading",
            "status_text": "   ·   ".join(parts),
        }
        self.broker.publish("progress", self.progress)

    def _handle_status(self, info):
        """A stage marker with no measurable percentage (loading a model
        into memory, starting transcription). Shows the user which step is
        running - as an indeterminate bar - so the flat stretches before
        the first segment don't read as a hang."""
        self.progress = {
            "pct": 0.0,
            "stage": info.get("stage", ""),
            "indeterminate": True,
            "status_text": info.get("text", ""),
        }
        self.broker.publish("progress", self.progress)

    def _on_done(self, output_path):
        self.phase = "done"
        self.last_output = output_path
        self.progress = {"pct": 100.0, "status_text": "Done"}
        self.broker.publish("progress", self.progress)
        self._append_log("\n=== Done ===\n")
        if output_path and Path(output_path).exists():
            _recent_add(output_path, reviewed=False, verified=False)
            self.broker.publish("recents", {})
        self._publish_run_state()

    def _run_ended(self):
        if self.on_run_ended is not None:
            try:
                self.on_run_ended()
            except Exception:
                _log("run-ended hook failed:\n" + traceback.format_exc())

    def _on_error(self, message):
        self.phase = "error"
        first_line = (message.splitlines()[0] if message
                      else "Unknown error")
        self._append_log(f"\n!!! Error !!!\n{message}\n")
        self._publish_run_state(message=message, first_line=first_line)
        self._run_ended()

    def _on_cancelled(self, message):
        self.phase = "cancelled"
        if message:
            self._append_log(f"\n=== Stopped: {message} ===\n")
        else:
            self._append_log("\n=== Stopped ===\n")
        self._publish_run_state()
        self._run_ended()

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
                _recent_add(output_path, reviewed=False,
                            verified=False)
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
