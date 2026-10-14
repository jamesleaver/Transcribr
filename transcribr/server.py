"""The loopback HTTP server: static front-end, JSON API, SSE stream."""

from pathlib import Path
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import traceback

from .version import ABOUT_TEXT, __version__
from .errors import ApiFail
from .palettes import _PALETTES
from .platformutil import (
    _AUDIO_EXTENSIONS, _REVEAL_LABEL, _annotations_file, _annotations_load,
    _autosave_clear, _autosave_load, _find_readme, _log, _log_file_path,
    _open_path, _open_url, _recent_add, _recent_load, _recent_save,
    _reveal_path, _settings_save, _terms_load, _terms_save,
    annotate_enabled, annotation_add, annotation_delete, annotations_clear
)
from .paragraphs import _have_pyav
from .readers import search_transcripts
from .engines import (
    ALL_MODELS, ENGINE_AUTO_NAME, LANGUAGES, MODEL_TIERS, WHISPER_MODELS,
    available_engines
)
from .presets import PRESETS
from .diarize import DIARIZE_VOICE_MODELS
from .worker import extract_terms
from .settings import current_settings, validate_settings
from .broker import _RESYNC, EventBroker, _sse_format
from .runctl import RunController
from .models import ModelController
from .updates import UpdateChecker
from .audioprep import (
    AudioPrep, RetransJob, _AUDIO_MIMETYPES, auto_retranscribe,
    repair_settings
)
from .review import ReviewSession, autosave_restore_info, open_transcript_info


#
# The web UI is served entirely from a loopback-only HTTP server: the
# static front-end build (webdist/), a JSON API, and a Server-Sent
# Events stream. Every /api/ and /audio/ request must present the
# per-session token - header X-Transcribr-Token, or ?token= for the
# two GETs that cannot set headers (EventSource and <audio>). bottle
# is imported lazily so `import transcribr` works without it.

def _webdist_dir():
    # webdist/ sits beside the package, not inside it.
    return Path(__file__).resolve().parent.parent / "webdist"


class WebBackend:
    """Everything the web route handlers need, plus server plumbing."""

    def __init__(self, token):
        self.token = token
        self.broker = EventBroker()
        self.controller = RunController(self.broker)
        self.controller.on_paragraphs_ready = self._open_review_fresh
        self.controller.on_transcript_started = self._open_review_live
        self.controller.on_paragraphs_grew = self._grow_review
        self.controller.on_run_ended = self._seal_live_review
        self.models = ModelController(self.broker, self.controller)
        self.controller.model_busy_check = self.models.is_busy
        self.updates = UpdateChecker(self.broker)
        if current_settings().get("check_updates", True):
            self.updates.check_async()
        self.review = None        # the open ReviewSession, if any
        self.audio = None         # AudioPrep for the open session
        self.retrans = None       # RetransJob for the open session
        self.window = None        # pywebview window (web mode only)
        self.server = None        # set by serve()
        self.has_window = False   # True once a pywebview window owns us
        self._pump_stop = threading.Event()

    def _open_review_live(self, info):
        """The run has started: open an empty session for it so the
        pane can fill in as paragraphs are finished."""
        session = ReviewSession.open_live(
            info, self.broker, log=self.controller._append_log)
        self._attach_audio(session)
        self.review = session
        self.broker.publish("review_opened", {"review": session.payload()})

    def _grow_review(self, data):
        """Newly finished paragraphs from the engine."""
        session = self.review
        if session is None or not session.live:
            return
        delta = session.grow(data.get("sealed") or [], data.get("tail"))
        if delta is not None:
            self.broker.publish("review_grew", delta)

    def _seal_live_review(self):
        """However a run ended, a session that filled in during it must
        not be left live.

        A stopped or failed run may never send paragraphs_ready - the
        engine process can be terminated before it gets that far - and
        a session left live can never be saved, so the partial
        transcript somebody asked to keep would be trapped in the pane
        with no way out."""
        session = self.review
        if session is None or session.closed or not session.live:
            return
        payload = session.finish_live({"result": None})
        self.controller._append_log(
            f"\n{len(session.model.paragraphs)} paragraphs kept - "
            "ready to save.\n")
        if payload is not None:
            self.broker.publish("review_opened", {"review": payload})

    def _open_review_fresh(self, info):
        """paragraphs_ready arrived from the worker (review-before-save
        run): finish the session and tell every client. Runs on the pump
        thread."""
        # The repair pass can take a while, so it happens off the pump
        # thread with the run still showing as in progress.
        threading.Thread(target=self._finish_fresh_review, args=(info,),
                         daemon=True,
                         name="transcribr-second-pass").start()

    def _finish_fresh_review(self, info):
        """The transcription is done. Either the pane has been filling
        in all along and only needs finishing, or it opens now."""
        live = self.review if (self.review is not None
                               and self.review.live) else None
        if live is None:
            # Nothing has been watching, so the repair pass can run
            # before the pane exists and hand over a finished
            # transcript rather than one that rewrites itself.
            try:
                info = self._second_pass(info)
            except Exception:
                _log("second pass failed; opening the review with the "
                     "first-pass transcript:\n" + traceback.format_exc())
        self.controller.phase = "idle"
        self.controller.progress = None
        self.controller._append_log(
            f"\n{len(info['paragraphs'])} paragraphs ready for review.\n")
        self.controller._publish_run_state()
        if live is not None:
            payload = live.finish_live(info)
            if payload is not None:
                self.broker.publish("review_opened", {"review": payload})
            self._repair_escalating(live)
            return
        session = ReviewSession.from_fresh(
            info, self.broker, log=self.controller._append_log)
        self._attach_audio(session)
        self.review = session
        self.broker.publish("review_opened", {"review": session.payload()})

    def _repair_into(self, session, *, tier=2, runs_first=None):
        """Re-read the sections that look like engine error, into a
        session somebody may already be editing.

        Tier 1 uses a small model and runs as soon as the transcription
        finishes; whatever it cannot fix is escalated to tier 2, which
        uses the model the run itself used, once the main engine has
        released its memory.

        The spans come back as indices into the transcript as it stood
        when the pass began, and indices move when a paragraph is
        deleted or split. They are resolved through paragraph ids
        instead, so a span that has been edited away is dropped rather
        than applied to whatever now sits at those numbers. Anything
        the reviewer has corrected is offered rather than replaced."""
        return self._run_repair(session, tier)

    def _repair_escalating(self, session):
        """Both tiers in order: the cheap attempt, then the full one on
        whatever is still flagged."""
        if not current_settings().get("auto_retranscribe", True):
            return
        fixed_any = self._run_repair(session, 1)
        still = self._run_repair(session, 2)
        if fixed_any or still:
            self.broker.publish("review_opened",
                                {"review": session.payload()})

    def _run_repair(self, session, tier):
        """Re-read the sections that look like engine error, into a
        session somebody may already be editing.

        The spans come back as indices into the transcript as it stood
        when the pass began, and indices move when a paragraph is
        deleted or split. They are resolved through paragraph ids
        instead, so a span that has been edited away is dropped rather
        than applied to whatever now sits at those numbers. Anything
        the reviewer has corrected is offered rather than replaced."""
        settings = repair_settings(current_settings(), tier=tier)
        if settings is None:
            return False        # nothing this tier can usefully do
        audio = session.audio_path
        if not audio or not Path(audio).exists():
            return False
        m = session.model
        with session.lock:
            before_ids = list(m.ids)
            paragraphs = [list(p) for p in m.paragraphs]
            words = list(m.word_conf or [])

        def progress(pct, text):
            self.controller.progress = {
                "pct": pct, "status_text": text, "stage": "retranscribing"}
            self.broker.publish("progress", self.controller.progress)

        label = ("a quick re-read with a small model" if tier == 1
                 else "a full re-read with the run's own model")
        try:
            _p, _w, _attempted, runs = auto_retranscribe(
                paragraphs, words, audio, settings, label=label,
                log=self.controller._append_log, progress=progress)
        except Exception:
            _log(f"repair pass (tier {tier}) failed:\n"
                 + traceback.format_exc())
            return False
        finally:
            self.controller.progress = None
            self.broker.publish("progress", None)

        applied = offered = 0
        with session.lock:
            if session.closed:
                return False
            index = {pid: i for i, pid in enumerate(m.ids)}
            for lo, hi, candidate in reversed(runs):
                lo_i = index.get(before_ids[lo])
                hi_i = index.get(before_ids[hi])
                if lo_i is None or hi_i is None or hi_i < lo_i:
                    continue            # edited away while we worked
                outcome = m.offer_engine_run(lo_i, hi_i, candidate,
                                             source="repair")
                if outcome == "applied":
                    applied += 1
                    index = {pid: i for i, pid in enumerate(m.ids)}
                elif outcome == "offered":
                    offered += 1
        if offered:
            self.controller._append_log(
                f"{offered} re-read "
                f"{'section is' if offered == 1 else 'sections are'} "
                "offered against text you had already corrected.\n")
        return bool(applied or offered)

    def _second_pass(self, info):
        """Re-run the sections that look like engine error, when the
        setting allows and there is audio to re-read. Fresh runs only: a
        transcript loaded from a file carries synthesised timings, so a
        span computed from them would not line up with the recording."""
        if not current_settings().get("auto_retranscribe", True):
            return info
        audio = info.get("audio_path")
        if not audio or not Path(audio).exists():
            return info

        def progress(pct, text):
            self.controller.progress = {
                "pct": pct, "status_text": text, "stage": "retranscribing"}
            self.broker.publish("progress", self.controller.progress)

        paragraphs, word_conf, attempted, replaced = auto_retranscribe(
            info["paragraphs"], info.get("word_conf"), audio,
            current_settings(),
            log=self.controller._append_log, progress=progress)
        if attempted:
            info = dict(info)
            info["paragraphs"] = paragraphs
            info["word_conf"] = word_conf or None
            # Detected speaker labels are positional, and the pass can
            # change how many paragraphs there are. Dropping them is
            # better than pinning the wrong name to the wrong voice.
            if replaced and info.get("preset_speakers"):
                info["preset_speakers"] = None
                info["diarized"] = False
        return info

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
                            or bottle.request.query.get("token") or "")
                # Constant-time: a plain != returns as soon as two
                # characters differ, which over enough requests tells a
                # local process how much of the token it has guessed.
                # The server is loopback-only so this is a small door,
                # but compare_digest is the same one line.
                if not secrets.compare_digest(str(supplied),
                                              str(backend.token)):
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
            import importlib.util as _ilu
            return {
                "version": __version__,
                "about_text": ABOUT_TEXT,
                "platform": sys.platform,
                "reveal_label": _REVEAL_LABEL,
                "ui_mode": "webview" if backend.has_window else "browser",
                "engines": ([{"key": "auto", "name": ENGINE_AUTO_NAME}]
                            + [{"key": k, "name": n}
                               for k, n in available_engines()]),
                "models": list(ALL_MODELS),
                "model_tiers": MODEL_TIERS,
                "presets": PRESETS,
                "languages": [[n, c] for n, c in LANGUAGES],
                "palettes": _PALETTES,
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "pyav": _have_pyav(),
                "diarize_available": (
                    _ilu.find_spec("sherpa_onnx") is not None),
                "diarize_models": [
                    {"id": m["id"], "label": m["label"],
                     "note": m["note"], "size": m["size"]}
                    for m in DIARIZE_VOICE_MODELS],
                "annotate": annotate_enabled(),
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
            for item in _recent_load():
                p = item["path"]
                try:
                    exists = Path(p).exists()
                except OSError:
                    exists = False
                if exists:
                    recents.append({"path": p, "name": Path(p).name,
                                    "exists": True,
                                    "reviewed": item["reviewed"],
                                    "verified": item["verified"]})
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

        @app.get("/api/terms")
        def api_terms_get():
            """Terms confirmed alongside the given file. `for` is the
            recording or transcript being worked on; terms are filed by
            its folder."""
            for_path = bottle.request.query.get("for") or ""
            return {"terms": _terms_load(for_path),
                    "folder": Path(for_path).parent.name if for_path else ""}

        @app.put("/api/terms")
        def api_terms_put():
            """Replace the term list for a file's folder. The reviewer
            owns this: terms arrive by correcting a transcript, and
            leave by being removed here."""
            try:
                body = bottle.request.json
            except Exception:
                body = None
            if (not isinstance(body, dict)
                    or not isinstance(body.get("terms"), list)
                    or not str(body.get("for") or "").strip()):
                raise bottle.HTTPResponse(
                    body=json.dumps({"error": {
                        "code": "bad_request",
                        "message": ("Body must be "
                                    "{\"for\": path, \"terms\": [...]}.")}}),
                    status=400,
                    headers={"Content-Type": "application/json"})
            kept = _terms_save(str(body["for"]), body["terms"])
            return {"terms": kept}

        @app.get("/api/transcripts/search")
        def api_transcripts_search():
            """Search saved transcripts. Reads files on demand rather
            than keeping an index: transcripts are edited outside this
            app, and a stale index is worse than a slower search."""
            q = (bottle.request.query.get("q") or "").strip()
            if len(q) < 2:
                return {"results": [], "searched": 0, "unreadable": 0,
                        "query": q}
            found = search_transcripts(q)
            found["query"] = q
            return found

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

        @app.get("/api/review/terms")
        def api_review_terms():
            """Candidate names and terms in the transcript as it stands.
            Recomputed on request rather than carried in the review
            payload: the transcript changes as it is edited, and this is
            only wanted when the panel is open."""
            session = backend._live_review()
            with session.lock:
                paragraphs = [list(p) for p in session.model.paragraphs]
            return {"terms": extract_terms(paragraphs, min_count=1)}

        @app.post("/api/review/retranscribe")
        def api_review_retranscribe():
            body = bottle.request.json or {}
            try:
                session = backend._live_review()
                job = backend.retrans
                if job is not None and job.state == "running":
                    raise ApiFail(409, "busy",
                                  "A re-transcription is already "
                                  "running.")
                audio = session.audio_path
                if not audio or not Path(audio).exists():
                    raise ApiFail(
                        400, "no_audio",
                        "The source recording for this transcript "
                        "isn't known, so the section can't be re-run. "
                        "Use 'Locate audio…' on the Playback card "
                        "first.")
                lo = int(body.get("from", -1))
                hi = int(body.get("to", lo))
                with session.lock:
                    if not (0 <= lo <= hi
                            < len(session.model.paragraphs)):
                        raise ApiFail(400, "bad_request",
                                      "Bad paragraph range.")
                    rev = int(body.get("rev", -1))
                    session._check_rev(rev)
                settings = dict(current_settings())
                model = str(body.get("model") or "").strip()
                if model:
                    settings["model"] = model
                settings["condition_on_previous_text"] = bool(
                    body.get("condition"))
                backend.retrans = RetransJob(backend, session,
                                             settings, lo, hi)
            except ApiFail as e:
                _fail(e)
            return {"ok": True}

        @app.post("/api/review/retranscribe/cancel")
        def api_review_retranscribe_cancel():
            job = backend.retrans
            if job is not None:
                job.cancel_event.set()
            return {"ok": True}

        @app.post("/api/review/export")
        def api_review_export():
            body = bottle.request.json or {}
            try:
                session = backend._live_review()
                show_ts = body.get("show_timestamp")
                colour = body.get("colour_speakers")
                path = session.export(
                    int(body.get("rev", -1)),
                    fmt=str(body.get("format") or "pdf"),
                    show_timestamp=(bool(show_ts)
                                    if show_ts is not None else None),
                    colour_speakers=(bool(colour)
                                     if colour is not None else None),
                    verified_by=(str(body["verified_by"])
                                 if "verified_by" in body else None),
                    layout=(str(body["layout"]) if body.get("layout")
                            else None),
                    askers=([str(a) for a in body["askers"]]
                            if isinstance(body.get("askers"), list)
                            else None))
            except ApiFail as e:
                _fail(e)
            return {"out_path": path}

        @app.post("/api/review/audio")
        def api_review_audio():
            """Point the open review session at the source recording
            when the embedded/guessed path is missing (the review
            pane's "Locate audio…" button)."""
            body = bottle.request.json or {}
            path = (body.get("path") or "").strip()
            try:
                session = backend._live_review()
                if not path or not Path(path).exists():
                    raise ApiFail(400, "not_found",
                                  f"No such file:\n{path}")
                if Path(path).suffix.lower() not in _AUDIO_EXTENSIONS:
                    raise ApiFail(400, "not_audio",
                                  "That doesn't look like an audio or "
                                  "video file.")
                session.audio_path = path
                backend._attach_audio(session)
            except ApiFail as e:
                _fail(e)
            return {"ok": True}

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
                    fmt = body.get("format")
                    show_ts = body.get("show_timestamp")
                    colour = body.get("colour_speakers")
                    out_path = session.save(
                        int(body.get("rev", -1)),
                        str(body.get("mode", "labels")),
                        extra_queue=backend.controller.queue,
                        out_format=(str(fmt) if fmt else None),
                        show_timestamp=(bool(show_ts)
                                        if show_ts is not None else None),
                        colour_speakers=(bool(colour)
                                         if colour is not None else None),
                        verified_by=(str(body["verified_by"])
                                     if "verified_by" in body else None))
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
            # Name the type explicitly. bottle 0.13 dropped the magic
            # "auto" string, so passing it emits a literal
            # `Content-Type: auto` - WKWebView sniffs past that, but
            # stricter engines (WebView2) refuse to play the media.
            mimetype = _AUDIO_MIMETYPES.get(p.suffix.lower())
            resp = bottle.static_file(p.name, root=str(p.parent),
                                      mimetype=mimetype or True)
            # The URL names the *current* session's audio, which
            # changes between reviews - never let the browser cache it.
            resp.set_header("Cache-Control", "no-store")
            return resp

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

        # -- model manager ------------------------------------------------

        @app.get("/api/models")
        def api_models():
            return backend.models.list_payload()

        @app.post("/api/models/download")
        def api_models_download():
            body = bottle.request.json or {}
            try:
                backend.models.start_download(
                    str(body.get("engine", "")), str(body.get("model", "")))
                return {"ok": True}
            except ApiFail as e:
                _fail(e)

        @app.post("/api/models/download/cancel")
        def api_models_download_cancel():
            return {"ok": True, "cancelling": backend.models.cancel()}

        @app.post("/api/models/uninstall")
        def api_models_uninstall():
            body = bottle.request.json or {}
            try:
                freed = backend.models.uninstall(
                    str(body.get("engine", "")), str(body.get("model", "")))
                return {"ok": True, "freed": freed}
            except ApiFail as e:
                _fail(e)

        @app.post("/api/models/engine/install")
        def api_models_engine_install():
            body = bottle.request.json or {}
            try:
                backend.models.start_engine_install(
                    str(body.get("engine", "")))
                return {"ok": True}
            except ApiFail as e:
                _fail(e)

        @app.post("/api/models/engine/uninstall")
        def api_models_engine_uninstall():
            body = bottle.request.json or {}
            try:
                backend.models.start_engine_uninstall(
                    str(body.get("engine", "")))
                return {"ok": True}
            except ApiFail as e:
                _fail(e)

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

        @app.post("/api/url/open")
        def api_url_open():
            """Open an external link from the in-app README in the
            system browser / mail client."""
            body = bottle.request.json or {}
            url = (body.get("url") or "").strip()
            if not re.match(r"^(https?://|mailto:)", url, re.IGNORECASE):
                _fail(ApiFail(400, "bad_url",
                              "Only http(s) and mailto links open."))
            _open_url(url)
            return {"ok": True}

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

        @app.post("/api/notices/open")
        def api_notices_open():
            """Open the third-party notices. A build ships them beside
            the package; a source checkout writes them on first ask."""
            from .notices import bundled_notices_path, notices_text
            from .platformutil import _config_dir
            p = bundled_notices_path()
            if not p.exists():
                p = _config_dir() / "THIRD-PARTY-NOTICES.txt"
                try:
                    p.write_text(notices_text(), encoding="utf-8")
                except OSError as e:
                    _fail(ApiFail(500, "write_failed", str(e)))
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

        # -- developer annotations ----------------------------------------

        @app.get("/api/annotations")
        def api_annotations():
            return {"items": _annotations_load(),
                    "file": str(_annotations_file())}

        @app.post("/api/annotations")
        def api_annotations_add():
            body = bottle.request.json or {}
            try:
                return annotation_add(body)
            except ApiFail as e:
                _fail(e)

        @app.post("/api/annotations/delete")
        def api_annotations_delete():
            body = bottle.request.json or {}
            try:
                annotation_delete(int(body.get("id", -1)))
            except ApiFail as e:
                _fail(e)
            return {"ok": True}

        @app.post("/api/annotations/clear")
        def api_annotations_clear():
            annotations_clear()
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

        # -- updates -----------------------------------------------------

        @app.get("/api/update")
        def api_update_get():
            return backend.updates.status()

        @app.post("/api/update/check")
        def api_update_check():
            backend.updates.check_async()
            return backend.updates.status()

        @app.post("/api/update/install")
        def api_update_install():
            try:
                backend.updates.install_async()
            except ApiFail as e:
                _fail(e)
            return backend.updates.status()

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
                        if item is _RESYNC:
                            # This client fell too far behind to be
                            # caught up. Same answer as too old a
                            # Last-Event-ID: start again.
                            yield "event: resync\ndata: {}\n\n"
                            return
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
                pattern = ";".join(f"*{e}" for e in _AUDIO_EXTENSIONS)
                types = (f"Audio and video ({pattern})",
                         "All files (*.*)")
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
