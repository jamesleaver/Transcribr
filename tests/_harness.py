"""Shared test setup. Import this before importing transcribr.

The config directory is redirected here, once, for the life of the
process - not per module, and never put back.

That is deliberate. The review pane's autosave is a debounced
threading.Timer: a mutation schedules a write three seconds later. A
test that builds a session and does not close it leaves that timer
armed, and it can fire after the module's teardown has run. When the
redirect was undone in teardown, those late writes landed in the real
~/Library/Application Support/Transcribr - the suite scribbling a
fixture transcript over a user's actual crash-recovery file, which the
app would then offer to restore on next launch.

The application itself is not affected: every ReviewSession close path
goes through _finish(), which cancels the timer and clears the
autosave. It was only ever the tests, which have no reason to close
anything.

Keeping the redirect set for the whole process means a late timer
writes into the temp directory, where it belongs and where nobody will
ever look at it.
"""

import atexit
import os
import tempfile

# ignore_cleanup_errors: on Windows a directory cannot be removed while
# any file in it is still open, and a stray timer may still be writing
# as the process exits. A temp directory left behind is not worth an
# exception on the way out.
_tmp_config = tempfile.TemporaryDirectory(prefix="transcribr-tests-",
                                          ignore_cleanup_errors=True)

CONFIG_DIR = _tmp_config.name
os.environ["TRANSCRIBR_CONFIG_DIR"] = CONFIG_DIR

# The log is a separate directory with its own override. Tests
# exercise the error paths on purpose, and their tracebacks have no
# business in the log a user attaches to a bug report.
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
os.environ["TRANSCRIBR_LOG_DIR"] = LOG_DIR

# Not in a tearDownModule: cleanup has to happen after every last timer
# in the process has had its chance to fire.
atexit.register(_tmp_config.cleanup)


def real_config_dir():
    """Where the app would keep a real user's files - the directory the
    suite must never touch."""
    import sys
    from pathlib import Path
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Transcribr"
    if sys.platform == "win32":
        return (Path(os.environ.get("APPDATA",
                                    Path.home() / "AppData" / "Roaming"))
                / "Transcribr")
    return (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "Transcribr")
