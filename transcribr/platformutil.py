"""Per-user paths, logging, and every file the app keeps on disk.

Atomic writes and the schema envelope live here, so nothing else in
the app has to think about either."""

from pathlib import Path
import os
import re
import subprocess
import sys
import threading
import time
import traceback

from .version import __version__
from .errors import ApiFail


if sys.platform == "darwin":
    _REVEAL_LABEL = "Reveal in Finder"
elif sys.platform == "win32":
    _REVEAL_LABEL = "Show in Explorer"
else:
    _REVEAL_LABEL = "Show in Folder"


def _open_url(url):
    """Open an external link (http/https/mailto) in the system browser
    or mail client. Kept separate from _open_path so tests can stub it
    and so nothing file-like ever reaches the browser."""
    import webbrowser
    webbrowser.open(url)


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
    here = Path(__file__).resolve().parent      # the package directory
    candidates = [
        here.parent / "README.md",              # beside the package
        here.parent / "README.txt",
        here.parent.parent / "README.md",       # one level further out
        here.parent.parent / "README.txt",
        here / "README.md",
        here / "README.txt",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _log_dir():
    """Return a writable per-user directory for log files.

    TRANSCRIBR_LOG_DIR overrides it, which is how the test suite keeps
    its own noise out of the log a user would send in with a bug
    report - the same reason TRANSCRIBR_CONFIG_DIR exists."""
    override = os.environ.get("TRANSCRIBR_LOG_DIR", "").strip()
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
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


# Every audio/video extension the app accepts - deliberately broad,
# because source recordings arrive in whatever a police disc, dictation
# device or phone produced (.wma/.wmv ERISP exports, .dss/.ds2
# dictaphones, .amr/.3gp phones, .vob/.mpg disc rips, ...). PyAV's
# bundled FFmpeg decodes nearly all of them.
_AUDIO_EXTENSIONS = (
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".wma", ".wmv", ".asf", ".amr", ".3gp", ".3gpp", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".vob", ".flv",
    ".aif", ".aiff", ".caf", ".dss", ".ds2", ".dct", ".gsm",
)


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
    override = os.environ.get("TRANSCRIBR_CONFIG_DIR", "").strip()
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
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


# ---- Persisted files: atomic writes and schema versions ------------------
#
# Everything the app keeps under the config directory is written through
# _write_versioned() and read through _read_versioned().
#
# The write side matters because a plain json.dump onto the target file
# truncates it the moment the process dies mid-write - and the file most
# likely to be open when that happens is autosave.json, which exists
# precisely to survive a crash. Writing a sibling temp file and renaming
# it over the target means a reader sees either the whole old file or
# the whole new one, never half of each. os.replace is atomic on POSIX
# and on Windows.
#
# The read side matters because these files outlive the version that
# wrote them. Each now carries {"schema": N, "kind": ..., "data": ...}.
# A file written before that envelope existed is recognised by its shape
# and treated as schema 0, so an upgrade reads the old file, migrates it
# and writes the new form back without the user noticing. Changing a
# format means bumping the number in _SCHEMA and adding one function to
# _MIGRATIONS; nothing else in the app needs to know.

# Current schema of each persisted file. Schema 0 is reserved for the
# pre-envelope files written by 0.9.14 and earlier.
_SCHEMA = {
    "settings": 3,
    "recent": 1,
    "terms": 1,
    "annotations": 1,
    "autosave": 1,
}


def _write_json_atomic(path, data, *, indent=2, ensure_ascii=True):
    """Serialise `data` to `path` through a temp file in the same
    directory and an atomic rename. Raises on failure; each caller
    decides whether that is worth reporting."""
    import json
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent, ensure_ascii=ensure_ascii)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # The rename itself is a change to the directory, and it is not
        # durable until the directory is flushed too. Without this a
        # power loss between the rename and the next disk flush can take
        # the file with it - and the file most likely to be mid-write is
        # autosave.json, which exists only for the case where the
        # machine stops without warning. Directories cannot be opened
        # for this on Windows, where the rename is durable on its own.
        if sys.platform != "win32":
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass        # best effort: the data is already written
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_versioned(path, kind, data, *, indent=2, ensure_ascii=True):
    """Write `data` as the current schema for `kind`, atomically."""
    _write_json_atomic(path,
                       {"schema": _SCHEMA[kind], "kind": kind, "data": data},
                       indent=indent, ensure_ascii=ensure_ascii)


def _unwrap_versioned(kind, doc):
    """(schema, payload) for a loaded document. A file with no envelope
    predates it: schema 0, and the document itself is the payload."""
    if (isinstance(doc, dict) and doc.get("kind") == kind
            and isinstance(doc.get("schema"), int)):
        return doc["schema"], doc.get("data")
    return 0, doc


def _migrate_recent_0_to_1(payload):
    """Pre-0.9.5 recent.json held bare path strings; every entry now
    carries its reviewed/verified badges."""
    if not isinstance(payload, list):
        return []
    out = []
    for item in payload:
        if isinstance(item, str):
            out.append({"path": item, "reviewed": False, "verified": False})
        elif isinstance(item, dict) and item.get("path"):
            out.append({"path": str(item["path"]),
                        "reviewed": bool(item.get("reviewed")),
                        "verified": bool(item.get("verified"))})
    return out


def _migrate_settings_1_to_2(data):
    """Speaker detection's defaults changed: TitaNet replaces WeSpeaker
    as the voice model,
    CAM++ is withdrawn, the speaker count is asked for rather than
    guessed, and auto mode clusters at 0.9 rather than 0.5.

    Saving settings writes every value, defaults included, so a new
    default alone would never reach anyone who has saved a setting.
    Values still at the OLD default move to the new one; anything a
    person set to something else is theirs and is left alone. The old
    and new values are written out here rather than imported, so this
    step keeps meaning the same thing when the defaults move again."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    if data.get("diarize_model") in ("wespeaker", "campplus"):
        data["diarize_model"] = "titanet"
    if data.get("num_speakers") == 0:
        data["num_speakers"] = 2
    if data.get("diarize_threshold") == 0.5:
        data["diarize_threshold"] = 0.9
    return data


def _migrate_settings_2_to_3(data):
    """Transcription defaults changed: each window is no longer
    conditioned on the text before it, and paragraphs break at a 1.0 s pause instead of 1.5 s.
    As in the step above, only values still at the old default move."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    if data.get("condition_on_previous_text") is True:
        data["condition_on_previous_text"] = False
    if data.get("gap") == 1.5:
        data["gap"] = 1.0
    return data


# kind -> {from_version: migration}. A kind whose pre-envelope shape is
# already current needs no entry: the loop below simply steps over it.
_MIGRATIONS = {
    "recent": {0: _migrate_recent_0_to_1},
    "settings": {1: _migrate_settings_1_to_2, 2: _migrate_settings_2_to_3},
}


def _read_versioned(path, kind, default=None):
    """Load `path`, migrate the payload forward to the current schema
    for `kind`, and return it. Any read, parse or migration problem
    yields `default` - a damaged preference file must never be the
    reason the app won't start."""
    import json

    path = Path(path)
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        _log(f"Could not read {path.name}: {e}")
        return default

    version, payload = _unwrap_versioned(kind, doc)
    current = _SCHEMA[kind]
    if version > current:
        # Written by a newer Transcribr - the user has downgraded, or
        # runs two builds against one config directory. Every reader
        # below normalises what it is handed, so passing the payload
        # through loses less than discarding it would.
        _log(f"{path.name} is schema {version}; this build understands "
             f"{current}. Reading it as it stands.")
        return payload
    try:
        steps = _MIGRATIONS.get(kind, {})
        while version < current:
            step = steps.get(version)
            if step is not None:
                payload = step(payload)
            version += 1
    except Exception as e:
        _log(f"Could not migrate {path.name} from schema {version}: {e}")
        return default
    return payload


def _terms_path():
    return _config_dir() / "terms.json"


def _terms_folder_key(path):
    """The folder a recording or transcript belongs to, as the key its
    terms are filed under. Folders are the grouping people already use -
    a brief, a story, a study, a set of interviews all arrive as a
    folder of recordings - so nothing new has to be explained or
    maintained."""
    if not path:
        return ""
    try:
        return str(Path(path).resolve().parent)
    except (OSError, ValueError):
        return ""


def _terms_all():
    """{folder: [term, ...]} for every folder that has any."""
    data = _read_versioned(_terms_path(), "terms", default={})
    if not isinstance(data, dict):
        return {}
    folders = data.get("folders")
    if isinstance(folders, dict):
        return {str(k): [str(t) for t in v if str(t).strip()]
                for k, v in folders.items() if isinstance(v, list)}
    return {}


def _terms_load(path=None):
    """Terms confirmed in the folder `path` belongs to.

    Scoped to the folder on purpose. These names come from one set of
    recordings, and carrying them into an unrelated transcript would
    prime the engine to hear a name from somebody else's file - wrong
    for accuracy, and worse than wrong when the two belong to different
    people.
    """
    key = _terms_folder_key(path)
    if not key:
        return []
    return list(_terms_all().get(key, []))


def _terms_save(path, terms):
    """Replace the term list for `path`'s folder. No-op on error - a
    vocabulary note must never cost someone a transcript."""
    key = _terms_folder_key(path)
    if not key:
        return []
    cleaned, seen = [], set()
    for t in terms:
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            cleaned.append(t)
    del cleaned[200:]
    try:
        everything = _terms_all()
        if cleaned:
            everything[key] = cleaned
        else:
            everything.pop(key, None)
        _write_versioned(_terms_path(), "terms", {"folders": everything})
        return cleaned
    except OSError as e:
        _log(f"Could not write terms.json: {e}")
        return _terms_load(path)


def _terms_add(path, term):
    """Record a confirmed term against `path`'s folder, most recent
    first."""
    term = (term or "").strip()
    if not term or not _terms_folder_key(path):
        return _terms_load(path)
    kept = [t for t in _terms_load(path) if t.lower() != term.lower()]
    kept.insert(0, term)
    return _terms_save(path, kept)


def _recent_file():
    return _config_dir() / "recent.json"


def _recent_load():
    """Return the recent-transcript entries, most-recent first, each a
    dict {"path", "reviewed", "verified"}. Pre-0.9.5 files stored bare
    path strings; _migrate_recent_0_to_1 upgrades them on read.
    Best-effort; returns [] on any read/parse error."""
    data = _read_versioned(_recent_file(), "recent", default=[])
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("path"):
            out.append({"path": str(item["path"]),
                        "reviewed": bool(item.get("reviewed")),
                        "verified": bool(item.get("verified"))})
    return out


def _recent_save(items):
    try:
        norm = []
        for item in items:
            if isinstance(item, str):
                item = {"path": item, "reviewed": False,
                        "verified": False}
            norm.append(item)
        _write_versioned(_recent_file(), "recent", norm[:_RECENT_MAX])
    except Exception as e:
        _log(f"Could not write recent.json: {e}")


def _recent_add(path, reviewed=None, verified=None):
    """Move `path` to the front of the recent list (deduping). The
    reviewed/verified flags update the entry when given; None keeps
    whatever the entry already says (e.g. merely re-opening a reviewed
    transcript doesn't clear its badge). No-op on error."""
    try:
        path = str(Path(path).resolve())
    except Exception:
        path = str(path)
    items = _recent_load()
    prior = next((i for i in items if i["path"] == path), None)
    entry = {
        "path": path,
        "reviewed": (bool(reviewed) if reviewed is not None
                     else bool(prior and prior["reviewed"])),
        "verified": (bool(verified) if verified is not None
                     else bool(prior and prior["verified"])),
    }
    items = [i for i in items if i["path"] != path]
    items.insert(0, entry)
    _recent_save(items)


def _settings_file():
    return _config_dir() / "settings.json"


def _settings_load():
    """Return the saved UI settings dict, or {} on any read/parse error."""
    data = _read_versioned(_settings_file(), "settings", default={})
    return data if isinstance(data, dict) else {}


def _settings_save(settings):
    """Persist the UI settings dict. Best-effort; logs on failure."""
    try:
        _write_versioned(_settings_file(), "settings", dict(settings))
    except Exception as e:
        _log(f"Could not write settings.json: {e}")


# ---- Developer annotations ------------------------------------------------
#
# A development aid ported from the Chambers Availability tool: with
# `--annotate` (or TRANSCRIBR_ANNOTATE=1) a pencil overlay appears in
# the UI; clicking any element captures where it is (selector path,
# text, markup, geometry) and pins a note to it. Notes land in
# annotations.json under the config dir, ready to be pasted into a
# development session. The overlay is invisible to normal users.

ANNOTATE_MODE = False


def set_annotate_mode(on):
    """Turn the overlay on for this process. Set from the command line;
    a plain assignment from another module would bind a second copy
    that annotate_enabled() below never reads."""
    global ANNOTATE_MODE
    ANNOTATE_MODE = bool(on)


def annotate_enabled():
    """Whether the annotation overlay should show. Checked live so the
    marker file works without a restart: the --annotate flag, the
    TRANSCRIBR_ANNOTATE=1 environment variable, or an `annotate.on`
    file in the config dir (`touch` it to enable the overlay for the
    double-clicked app; delete it to hide again)."""
    if ANNOTATE_MODE:
        return True
    if os.environ.get("TRANSCRIBR_ANNOTATE", "").strip() == "1":
        return True
    try:
        return (_config_dir() / "annotate.on").exists()
    except OSError:
        return False


_annotations_lock = threading.Lock()

# Field -> stored size cap. Keeps a runaway page from bloating the file.
_ANNOTATION_FIELDS = {
    "view": 100, "selector": 500, "element_text": 500,
    "html": 4000, "rect": 300, "note": 4000,
}


def _annotations_file():
    return _config_dir() / "annotations.json"


def _annotations_load():
    data = _read_versioned(_annotations_file(), "annotations", default=[])
    return data if isinstance(data, list) else []


def _annotations_save(items):
    _write_versioned(_annotations_file(), "annotations", items,
                     ensure_ascii=False)


def annotation_add(body):
    """Validate + append one annotation; returns the stored record."""
    record = {}
    for key, cap in _ANNOTATION_FIELDS.items():
        record[key] = str(body.get(key) or "")[:cap]
    if not record["note"].strip():
        raise ApiFail(400, "empty_note", "The note is empty.")
    with _annotations_lock:
        items = _annotations_load()
        record["id"] = (max((a.get("id", 0) for a in items), default=0)
                        + 1)
        record["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
        record["app_version"] = __version__
        items.append(record)
        _annotations_save(items)
    return record


def annotation_delete(ann_id):
    with _annotations_lock:
        items = _annotations_load()
        kept = [a for a in items if a.get("id") != ann_id]
        if len(kept) == len(items):
            raise ApiFail(404, "not_found", f"No annotation #{ann_id}.")
        _annotations_save(kept)


def annotations_clear():
    with _annotations_lock:
        _annotations_save([])


# ---- Review auto-save (crash recovery) -----------------------------------
#
# While the review pane is open, the labelled-but-unsaved state is
# periodically written here. It is deleted on any clean exit from review;
# if it survives to the next launch, the app offers to restore it.

def _autosave_file():
    return _config_dir() / "autosave.json"


def _autosave_load():
    """Return the saved review session dict, or None."""
    data = _read_versioned(_autosave_file(), "autosave", default=None)
    if isinstance(data, dict) and data.get("paragraphs"):
        return data
    return None


def _autosave_save(data):
    try:
        # indent=None: this one is rewritten every few seconds while
        # the review pane is open, and nobody reads it by eye.
        _write_versioned(_autosave_file(), "autosave", data, indent=None)
    except Exception as e:
        _log(f"Could not write autosave.json: {e}")


def _autosave_clear():
    try:
        _autosave_file().unlink(missing_ok=True)
    except Exception:
        pass
