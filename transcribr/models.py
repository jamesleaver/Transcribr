"""Listing, downloading and removing models and optional engines."""

from pathlib import Path
import os
import sys
import threading

from .errors import ApiFail, _CancelledByUser
from .engines import (
    WHISPER_MODELS, _macos_supports_mlx, _mlx_repo_for, _redetect_engines,
    available_engines
)
from .worker import _DownloadMonitor, _humanize_bytes


#
# Whisper model weights are large (75 MB - 3 GB) and each engine caches
# its own copy in a different place: openai-whisper keeps a single .pt per
# model under ~/.cache/whisper, while faster-whisper and mlx-whisper both
# pull Hugging Face repos into ~/.cache/huggingface/hub. So the same model
# name can occupy disk two or three times over. The model manager reports
# what is cached (with sizes), downloads models on demand - including
# brand-new ones released after this build, for the HF-backed engines -
# and lets the user reclaim space by uninstalling.


def _whisper_cache_dir():
    """Where openai-whisper stores its .pt files. Mirrors whisper's own
    default so we look in exactly the directory it downloads to
    ($XDG_CACHE_HOME/whisper, else ~/.cache/whisper)."""
    root = os.getenv("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return Path(root) / "whisper"


def _hf_cache_dir():
    """The huggingface_hub download cache (faster-whisper, mlx-whisper).
    Prefer the library's resolved constant so HF_HOME / HF_HUB_CACHE
    overrides are honoured; fall back to the documented default."""
    try:
        from huggingface_hub import constants as _hf_const
        return Path(_hf_const.HF_HUB_CACHE)
    except Exception:
        base = os.getenv("HF_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface")
        return Path(base) / "hub"


# openai-whisper aliases share one .pt file (large -> large-v3,
# turbo -> large-v3-turbo). The authoritative filename is the basename of
# whisper._MODELS[model]; this static map is the fallback when whisper
# can't be imported (e.g. in tests).
_WHISPER_PT_FALLBACK = {
    "tiny.en": "tiny.en.pt", "tiny": "tiny.pt",
    "base.en": "base.en.pt", "base": "base.pt",
    "small.en": "small.en.pt", "small": "small.pt",
    "medium.en": "medium.en.pt", "medium": "medium.pt",
    "large-v1": "large-v1.pt", "large-v2": "large-v2.pt",
    "large-v3": "large-v3.pt", "large": "large-v3.pt",
    "turbo": "large-v3-turbo.pt", "large-v3-turbo": "large-v3-turbo.pt",
}


def _whisper_pt_filename(model):
    """The cache filename openai-whisper uses for `model` (basename of its
    download URL), or None if the name is unknown."""
    try:
        import whisper
        url = whisper._MODELS.get(model)
        if url:
            return os.path.basename(url)
    except Exception:
        pass
    return _WHISPER_PT_FALLBACK.get(model)


def _faster_repo_for(model):
    """The Hugging Face repo faster-whisper caches `model` under.
    Authoritative from faster_whisper.utils._MODELS; falls back to the
    Systran naming used for the standard models."""
    try:
        from faster_whisper import utils as _fw_utils
        repo = getattr(_fw_utils, "_MODELS", {}).get(model)
        if repo:
            return repo
    except Exception:
        pass
    return f"Systran/faster-whisper-{model}"


def _repo_belongs_to_engine(engine, repo_id):
    """Heuristic: does a cached HF repo look like a Whisper model for this
    engine? Used to surface user-downloaded 'new' models that aren't in
    the built-in list."""
    r = repo_id.lower()
    if engine == "faster":
        return "faster-whisper" in r or "faster-distil-whisper" in r
    if engine == "mlx":
        return "whisper" in r and "mlx" in r
    return False


def _hf_repo_sizes():
    """Map {repo_id: (size_on_disk_bytes, [revision_hashes])} for every
    repo in the huggingface_hub cache. Empty when the library or the cache
    directory is absent. Uses the official scanner, which accounts for
    shared blobs so sizes aren't double-counted."""
    out = {}
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
    except Exception:
        return out
    for repo in info.repos:
        hashes = [r.commit_hash for r in repo.revisions]
        out[repo.repo_id] = (int(repo.size_on_disk), hashes)
    return out


def _download_status_text(model, downloaded, total, speed):
    parts = [f"Downloading '{model}'",
             f"{_humanize_bytes(downloaded)} of {_humanize_bytes(total)}"]
    if speed > 0:
        parts.append(f"{_humanize_bytes(speed)}/s")
    return "   ·   ".join(parts)


# Engines the app can pip-install on demand from the Models view, so the
# base install stays lean. faster-whisper ships with every install and is
# not listed here; the other two both drag in PyTorch, so they are opt-in.
#
# mlx-whisper used to be installed up front by the script installer, and
# was left out of the signed packages for size. That combination left
# Apple Silicon users with no route to GPU transcription at all, so it is
# offered here instead - gated to machines that can actually run it.
# mlx-whisper declares PyTorch as a dependency but uses it only to
# convert model files, never to transcribe. So its real runtime
# dependencies are installed first and mlx-whisper itself with
# --no-deps, which saves
# about a gigabyte. macos/build-pkg.sh bundles the same list.
MLX_RUNTIME_DEPS = ["mlx>=0.11", "numba", "numpy", "tqdm", "more-itertools",
                    "tiktoken", "huggingface_hub", "scipy"]

_INSTALLABLE_ENGINES = {
    "mlx": {
        "name": "mlx-whisper (Apple Silicon GPU)",
        "note": "Runs on the Mac's GPU via Apple's MLX - the fastest "
                "option on M-series machines.",
        "packages": MLX_RUNTIME_DEPS,
        "no_deps": ["mlx-whisper"],
        "uninstall": ["mlx-whisper", "mlx"],
        "verify_import": "mlx_whisper",
        "approx_mb": 350,
        "requires": _macos_supports_mlx,
    },
    "whisper": {
        "name": "OpenAI Whisper (reference)",
        "note": "The reference engine. Downloads PyTorch (~2 GB); "
                "slower than faster-whisper with essentially identical "
                "output.",
        "packages": ["openai-whisper>=20250625"],
        # For a clean uninstall we also drop torch, which nothing else in
        # this app uses - that's where the space actually goes.
        "uninstall": ["openai-whisper", "torch"],
        # Verified after install: a torch/numpy mismatch can leave the
        # package importable-by-metadata but broken at run time.
        "verify_import": "whisper",
        "approx_mb": 2200,
    },
}


def _engine_install_args(key):
    """pip-install arguments for an installable engine. openai-whisper
    needs numpy kept in a range its numba can import; Intel macOS needs
    the whole torch chain pinned back on top of that."""
    spec = _INSTALLABLE_ENGINES[key]
    pkgs = list(spec["packages"])
    if key == "whisper":
        import platform as _plat
        if sys.platform == "darwin" and _plat.machine() == "x86_64":
            # Intel macOS: PyTorch stopped shipping x86_64 wheels after
            # 2.2.2 (which predates numpy 2.0), so pin the whole chain.
            pkgs += ["torch==2.2.2", "numpy<2", "numba<0.60"]
        else:
            # openai-whisper pulls in numba, which lags numpy and refuses
            # to import under a too-new numpy ("Numba needs NumPy 2.4 or
            # less. Got NumPy 2.5."). faster-whisper may already have
            # pulled a newer numpy into the shared venv, so cap it to a
            # numba-compatible version (pip will downgrade if needed).
            # Bump this ceiling when numba gains newer-numpy support.
            pkgs += ["numpy<2.5"]
    return ["--upgrade", "--prefer-binary", *pkgs]


def _engine_uninstall_pkgs(key):
    """Packages to remove for `key`. PyTorch is a dependency of both
    optional engines, so it is only dropped when the other one is not
    installed - otherwise removing openai-whisper would silently break
    mlx-whisper, which is what makes the machine fast."""
    spec = _INSTALLABLE_ENGINES[key]
    pkgs = list(spec.get("uninstall") or spec["packages"])
    if "torch" in pkgs:
        import importlib.util as _ilu
        others = {"whisper": "mlx_whisper", "mlx": "whisper"}
        other = others.get(key)
        if other and _ilu.find_spec(other) is not None:
            pkgs = [p for p in pkgs if p != "torch"]
    return pkgs


def _pip_stream(args, on_line, should_cancel):
    """Run `python -m pip <args>` in a subprocess, forwarding each output
    line to on_line and honouring should_cancel(). Raises _CancelledByUser
    on cancel, RuntimeError on a non-zero exit."""
    import subprocess
    import queue as _queue
    env = dict(os.environ)
    if sys.platform == "darwin":
        # Match the installer: a well-formed deployment target keeps
        # Apple's clang from rejecting the build and nudges pip toward
        # pre-built wheels rather than source builds.
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")
    proc = subprocess.Popen(
        [sys.executable, "-m", "pip", *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, env=env)
    lines = _queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                lines.put(line.rstrip("\n"))
        finally:
            proc.stdout.close()

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    while True:
        try:
            on_line(lines.get(timeout=0.3))
        except _queue.Empty:
            pass
        if should_cancel() and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise _CancelledByUser()
        if proc.poll() is not None and lines.empty():
            break
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"pip exited with code {rc}")


def _run_engine_op(action, key, on_line, should_cancel):
    """Install or uninstall an engine's packages with pip, then (for an
    install) verify the engine actually imports. A torch/numpy mismatch
    can leave the package importable-by-metadata but broken at run time -
    verifying here turns that into a clear failure, and we roll the broken
    packages back so the app doesn't offer an engine that crashes."""
    if action == "install":
        _pip_stream(["install", *_engine_install_args(key)],
                    on_line, should_cancel)
        extra = _INSTALLABLE_ENGINES[key].get("no_deps")
        if extra:
            _pip_stream(["install", "--upgrade", "--prefer-binary",
                         "--no-deps", *extra], on_line, should_cancel)
        mod = _INSTALLABLE_ENGINES[key].get("verify_import")
        if mod:
            import subprocess
            on_line(f"Verifying {mod} imports cleanly...")
            check = subprocess.run([sys.executable, "-c", f"import {mod}"],
                                   capture_output=True, text=True)
            if check.returncode != 0:
                tail = (check.stderr or check.stdout or "").strip()
                last = tail.splitlines()[-1] if tail else "import failed"
                on_line("Import check failed; rolling back...")
                try:
                    _pip_stream(["uninstall", "-y",
                                 *_engine_uninstall_pkgs(key)],
                                lambda _l: None, lambda: False)
                except Exception:
                    pass
                raise RuntimeError(
                    f"{key} was installed but fails to import: {last}")
    else:
        _pip_stream(["uninstall", "-y", *_engine_uninstall_pkgs(key)],
                    on_line, should_cancel)


def _dedupe_alias_models(raw):
    """Collapse models that resolve to the same storage into one entry.

    `raw` is a list of (model, storage_key, installed, size) in preference
    order. Aliases share weights - openai-whisper's 'large' is the same
    .pt as 'large-v3', 'turbo' the same as 'large-v3-turbo', and the
    HF-backed engines map those pairs to one repo too - so showing both
    is redundant and would double-count. Each unique storage_key becomes
    one entry: the most descriptive (longest) name is canonical, the
    rest are listed as `aliases`."""
    groups, order = {}, []
    for model, skey, installed, size in raw:
        if skey not in groups:
            groups[skey] = []
            order.append(skey)
        groups[skey].append((model, installed, size))
    out = []
    for skey in order:
        members = groups[skey]
        names = [m for m, _i, _s in members]
        canonical = max(names, key=len)
        out.append({
            "model": canonical,
            "aliases": [n for n in names if n != canonical],
            "installed": any(i for _m, i, _s in members),
            "size": max((s for _m, _i, s in members), default=0),
            "storage_key": skey,
            "custom": False,
        })
    return out


class ModelStore:
    """Read-only view of which Whisper models are cached on disk, per
    installed engine, with sizes. `engines` defaults to the detected
    engines but is injectable for tests."""

    def __init__(self, engines=None):
        self.engines = (list(engines) if engines is not None
                        else list(available_engines()))

    def payload(self):
        hf_sizes = None
        engines_out = []
        for key, name in self.engines:
            if key == "whisper":
                engines_out.append(self._openai_engine(key, name))
            elif key in ("faster", "mlx"):
                if hf_sizes is None:
                    hf_sizes = _hf_repo_sizes()
                engines_out.append(self._hf_engine(key, name, hf_sizes))
        if _parakeet_available():
            engines_out.append(_parakeet_payload())
        installed_keys = {k for k, _ in self.engines}
        installable = [
            {"key": key, "name": spec["name"], "note": spec["note"],
             "approx_mb": spec.get("approx_mb", 0)}
            for key, spec in _INSTALLABLE_ENGINES.items()
            if key not in installed_keys
            and (spec.get("requires") or (lambda: True))()
        ]
        # An installed engine that the app knows how to pip-install can also
        # be removed to reclaim space.
        for e in engines_out:
            e["removable"] = e["key"] in _INSTALLABLE_ENGINES
        return {
            "whisper_cache": str(_whisper_cache_dir()),
            "hf_cache": str(_hf_cache_dir()),
            "engines": engines_out,
            "installable": installable,
            "total": sum(e["total"] for e in engines_out),
        }

    def _openai_engine(self, key, name):
        raw = []
        for m in WHISPER_MODELS:
            fn = _whisper_pt_filename(m)
            size, installed = 0, False
            if fn:
                p = _whisper_cache_dir() / fn
                try:
                    if p.exists():
                        size = p.stat().st_size
                        installed = size > 0
                except OSError:
                    pass
            raw.append((m, fn or m, installed, size))
        models = _dedupe_alias_models(raw)
        total = sum(e["size"] for e in models if e["installed"])
        return {"key": key, "name": name, "supports_custom": False,
                "models": models, "total": total}

    def _hf_engine(self, key, name, hf_sizes):
        repo_of = _faster_repo_for if key == "faster" else _mlx_repo_for
        raw, known_repos = [], set()
        for m in WHISPER_MODELS:
            repo = repo_of(m)
            known_repos.add(repo)
            entry = hf_sizes.get(repo)
            raw.append((m, repo, bool(entry), entry[0] if entry else 0))
        models = _dedupe_alias_models(raw)
        total = sum(e["size"] for e in models if e["installed"])
        # Models the user fetched that aren't in the built-in list
        # (e.g. a newly released repo). Surfaced so they can be removed.
        custom = []
        for repo, (size, _hashes) in hf_sizes.items():
            if repo in known_repos or not _repo_belongs_to_engine(key, repo):
                continue
            custom.append({"model": repo, "aliases": [], "installed": True,
                           "size": size, "storage_key": repo, "custom": True})
            total += size
        models.extend(sorted(custom, key=lambda e: e["model"]))
        return {"key": key, "name": name, "supports_custom": True,
                "models": models, "total": total}


def _parakeet_available():
    """Parakeet needs sherpa-onnx, which every build ships."""
    import importlib.util as _ilu
    return _ilu.find_spec("sherpa_onnx") is not None


def _parakeet_payload():
    """The Models page entry for Parakeet: one model, downloadable ahead
    of a run and removable afterwards. It is not a Whisper engine, so it
    has no model list to choose from."""
    from .engines import PARAKEET_MODEL
    from . import parakeet
    size = parakeet.model_size()
    return {
        "key": "parakeet",
        "name": "Parakeet (NVIDIA) — English only, ~480 MB",
        "supports_custom": False, "removable": False,
        "models": [{"model": PARAKEET_MODEL, "aliases": [],
                    "installed": parakeet.model_ready(), "size": size,
                    "storage_key": PARAKEET_MODEL}],
        "total": size,
    }


def _accepts(fn, name):
    import inspect
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _prefetch_model(engine, model, on_progress=None):
    """Download a model's weights without loading them. Runs on the model
    worker thread inside a _DownloadMonitor so progress is reported."""
    if engine == "whisper":
        import whisper
        url = whisper._MODELS.get(model)
        if not url:
            raise RuntimeError(
                f"'{model}' is not a known openai-whisper model.")
        root = str(_whisper_cache_dir())
        os.makedirs(root, exist_ok=True)
        whisper._download(url, root, in_memory=False)
    elif engine == "faster":
        from faster_whisper import download_model
        download_model(model)          # accepts a size name or a HF repo id
    elif engine == "mlx":
        from huggingface_hub import snapshot_download
        snapshot_download(model if "/" in model else _mlx_repo_for(model))
    elif engine == "parakeet":
        import queue as _queue
        from . import parakeet
        parakeet.ensure_model(_queue.Queue(), on_progress=on_progress)
    else:
        raise RuntimeError(f"Engine '{engine}' cannot download models.")


def _uninstall_model(engine, model):
    """Delete the cached weights for (engine, model). Returns bytes freed.
    Raises ApiFail when the model isn't present or a delete fails."""
    if engine == "whisper":
        fn = _whisper_pt_filename(model)
        p = (_whisper_cache_dir() / fn) if fn else None
        if p is None or not p.exists():
            raise ApiFail(404, "not_installed",
                          f"'{model}' is not downloaded for this engine.")
        try:
            size = p.stat().st_size
            p.unlink()
        except OSError as e:
            raise ApiFail(500, "delete_failed", f"Could not delete: {e}")
        return size
    if engine == "parakeet":
        from . import parakeet
        if not parakeet.model_ready():
            raise ApiFail(404, "not_installed",
                          "Parakeet is not downloaded.")
        return parakeet.remove_model()
    if engine in ("faster", "mlx"):
        if "/" in model:
            repo = model
        else:
            repo = (_faster_repo_for(model) if engine == "faster"
                    else _mlx_repo_for(model))
        return _hf_delete_repo(repo)
    raise ApiFail(400, "bad_engine", f"Engine '{engine}' is not installed.")


def _hf_delete_repo(repo_id):
    entry = _hf_repo_sizes().get(repo_id)
    if not entry:
        raise ApiFail(404, "not_installed",
                      f"'{repo_id}' is not in the download cache.")
    size, hashes = entry
    try:
        from huggingface_hub import scan_cache_dir
        scan_cache_dir().delete_revisions(*hashes).execute()
    except ApiFail:
        raise
    except Exception as e:
        raise ApiFail(500, "delete_failed", f"Could not delete: {e}")
    return size


class ModelController:
    """Downloads and uninstalls model weights on a background thread, one
    job at a time, publishing `model_progress` / `model_done` / `models`
    SSE events. Coupled loosely to the RunController so model work and
    transcription never overlap - loading or deleting weights mid-run
    would be unsafe. `worker_fn`/`store_fn` are injectable for tests."""

    def __init__(self, broker, run_controller=None, *,
                 prefetch_fn=None, uninstall_fn=None, store_fn=None,
                 engine_op_fn=None):
        self.broker = broker
        self.run_controller = run_controller
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.worker = None
        self.job = None            # {kind, ...} or None (one job at a time)
        self._prefetch = prefetch_fn or _prefetch_model
        self._uninstall = uninstall_fn or _uninstall_model
        self._store = store_fn or (lambda: ModelStore().payload())
        self._engine_op = engine_op_fn or _run_engine_op

    def _run_active(self):
        rc = self.run_controller
        return rc is not None and rc.phase in ("running", "stopping")

    def is_busy(self):
        with self.lock:
            return self.job is not None

    def list_payload(self):
        payload = self._store()
        with self.lock:
            payload["job"] = dict(self.job) if self.job else None
        payload["busy"] = self.is_busy() or self._run_active()
        return payload

    def _engine_installed(self, engine):
        if engine == "parakeet":
            return _parakeet_available()
        return engine in {k for k, _ in available_engines()}

    def start_download(self, engine, model):
        engine = (engine or "").strip()
        model = (model or "").strip()
        with self.lock:
            if self._run_active():
                raise ApiFail(409, "busy",
                              "A transcription is running. Wait for it to "
                              "finish before downloading a model.")
            if self.job is not None:
                raise ApiFail(409, "model_busy",
                              "Another model is already downloading.")
            if not self._engine_installed(engine):
                raise ApiFail(400, "bad_engine",
                              f"Engine '{engine}' is not installed.")
            if not model:
                raise ApiFail(400, "bad_model", "No model name was given.")
            if engine == "whisper":
                # openai-whisper can only fetch its fixed catalogue.
                try:
                    import whisper
                    known = model in whisper._MODELS
                except Exception:
                    known = model in _WHISPER_PT_FALLBACK
                if not known:
                    raise ApiFail(
                        400, "unknown_model",
                        f"'{model}' is not a known openai-whisper model. "
                        "New models can only be added for the faster-whisper "
                        "and mlx-whisper engines.")
            self.cancel_event.clear()
            self.job = {"kind": "download", "engine": engine, "model": model,
                        "phase": "starting", "pct": 0.0, "downloaded": 0,
                        "total": 0, "speed": 0.0,
                        "status_text": f"Preparing to download '{model}'…"}
            self._publish_progress()
            self.worker = threading.Thread(
                target=self._download_worker, args=(engine, model),
                daemon=True, name="transcribr-model-dl")
            self.worker.start()

    def _download_worker(self, engine, model):
        def on_progress(d):
            with self.lock:
                if self.job is None:
                    return
                total = d.get("total") or 0
                downloaded = d.get("downloaded") or 0
                speed = d.get("speed") or 0.0
                self.job.update({
                    "phase": "downloading",
                    "pct": max(0.0, min(100.0,
                                        (downloaded / total * 100)
                                        if total else 0.0)),
                    "downloaded": downloaded, "total": total, "speed": speed,
                    "status_text": _download_status_text(
                        model, downloaded, total, speed),
                })
            self._publish_progress()

        try:
            if engine == "parakeet":
                # Its own downloader reports progress; the monitor
                # watches the Whisper caches, which it never touches.
                kw = ({"on_progress": on_progress}
                      if _accepts(self._prefetch, "on_progress") else {})
                self._prefetch(engine, model, **kw)
            else:
                with _DownloadMonitor(self.cancel_event, model,
                                      on_progress=on_progress):
                    self._prefetch(engine, model)
        except _CancelledByUser:
            self._finish(model, cancelled=True)
            return
        except Exception as e:
            self._finish(model, error=f"{type(e).__name__}: {e}")
            return
        self._finish(model)

    def _finish(self, model, *, error=None, cancelled=False):
        with self.lock:
            self.job = None
        if error:
            self.broker.publish("model_done",
                                {"ok": False, "model": model, "error": error})
        elif cancelled:
            self.broker.publish(
                "model_done",
                {"ok": False, "model": model, "cancelled": True})
        else:
            self.broker.publish("model_done", {"ok": True, "model": model})
        self.broker.publish("models", self.list_payload())

    # ----- Engine install / uninstall (pip in the venv) ------------------

    def start_engine_install(self, key):
        return self._start_engine_op("install", key)

    def start_engine_uninstall(self, key):
        return self._start_engine_op("uninstall", key)

    def _start_engine_op(self, action, key):
        key = (key or "").strip()
        with self.lock:
            if self._run_active():
                raise ApiFail(409, "busy",
                              "A transcription is running. Wait for it to "
                              "finish first.")
            if self.job is not None:
                raise ApiFail(409, "model_busy",
                              "Another model or engine operation is already "
                              "in progress.")
            if key not in _INSTALLABLE_ENGINES:
                raise ApiFail(400, "bad_engine",
                              f"'{key}' is not an installable engine.")
            name = _INSTALLABLE_ENGINES[key]["name"]
            installed = self._engine_installed(key)
            if action == "install" and installed:
                raise ApiFail(409, "already_installed",
                              f"{name} is already installed.")
            if action == "uninstall" and not installed:
                raise ApiFail(404, "not_installed",
                              f"{name} isn't installed.")
            verb = "Installing" if action == "install" else "Removing"
            self.cancel_event.clear()
            self.job = {
                "kind": "engine", "action": action, "engine": key,
                "model": name,
                "phase": "installing" if action == "install" else "removing",
                "pct": 0.0,
                "status_text": f"{verb} {name}… (this can take a while)",
            }
            self._publish_progress()
            self.worker = threading.Thread(
                target=self._engine_worker, args=(action, key),
                daemon=True, name="transcribr-engine-op")
            self.worker.start()

    def _engine_worker(self, action, key):
        def on_line(line):
            line = (line or "").strip()
            if not line:
                return
            with self.lock:
                if self.job is None:
                    return
                self.job["status_text"] = line[:200]
            self._publish_progress()

        try:
            self._engine_op(action, key, on_line=on_line,
                            should_cancel=self.cancel_event.is_set)
        except _CancelledByUser:
            self._finish_engine(key, cancelled=True)
            return
        except Exception as e:
            self._finish_engine(key, error=f"{type(e).__name__}: {e}")
            return
        # Success: a newly (un)installed engine changes what's available.
        try:
            _redetect_engines()
        except Exception:
            pass
        self._finish_engine(key)

    def _finish_engine(self, key, *, error=None, cancelled=False):
        with self.lock:
            self.job = None
        name = _INSTALLABLE_ENGINES.get(key, {}).get("name", key)
        if error:
            self.broker.publish("model_done", {"ok": False, "engine": key,
                                               "model": name, "error": error})
        elif cancelled:
            self.broker.publish("model_done",
                                {"ok": False, "engine": key, "model": name,
                                 "cancelled": True})
        else:
            self.broker.publish("model_done", {"ok": True, "engine": key,
                                               "model": name})
            # The engine roster changed - tell clients to refresh /api/meta.
            self.broker.publish("engines_changed", {})
        self.broker.publish("models", self.list_payload())

    def cancel(self):
        with self.lock:
            if self.job is None:
                return False
            self.cancel_event.set()
            return True

    def uninstall(self, engine, model):
        with self.lock:
            if self._run_active():
                raise ApiFail(409, "busy",
                              "A transcription is running. Wait for it to "
                              "finish before removing a model.")
            if self.job is not None:
                raise ApiFail(409, "model_busy",
                              "A model is downloading. Wait for it to "
                              "finish before removing a model.")
        freed = self._uninstall((engine or "").strip(), (model or "").strip())
        self.broker.publish("models", self.list_payload())
        return freed

    def _publish_progress(self):
        with self.lock:
            job = dict(self.job) if self.job else {}
        self.broker.publish("model_progress", job)
