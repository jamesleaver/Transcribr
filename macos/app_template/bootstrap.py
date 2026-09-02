"""Transcribr - first-run setup, then hand over to the app.

The app bundle in /Applications is signed and must stay byte-for-byte
as it was notarised, so nothing may ever be written inside it. But the
Models tab installs optional engines with pip, and those have to land
somewhere. So each macOS user gets a small virtual environment under
their own Application Support folder, created from the bundled
interpreter with --system-site-packages: everything shipped in the
bundle stays visible, and anything installed later goes to the user's
own copy.

Creating it is local and quick - no downloads, no Homebrew, no
password - because every dependency the app needs is already inside the
bundle.
"""

import os
import subprocess
import sys
from pathlib import Path

BUNDLE_RESOURCES = Path(__file__).resolve().parent
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Transcribr"
VENV = APP_SUPPORT / "venv"
LOG_DIR = Path.home() / "Library" / "Logs" / "Transcribr"

# Bumped when the layout below changes in a way that makes an existing
# environment unusable, forcing a rebuild on next launch.
LAYOUT = "1"
STAMP = VENV / ".transcribr-bundle"


def _alert(message: str) -> None:
    """Tell the user something went wrong. stderr is invisible when the
    app is launched from Finder, so use a real dialog."""
    try:
        subprocess.run([
            "/usr/bin/osascript", "-e",
            'display alert "Transcribr" message "%s" as critical'
            % message.replace('"', "'").replace("\n", "\\n"),
        ], check=False)
    except Exception:
        pass
    print(message, file=sys.stderr)


def _stamp_value() -> str:
    return f"{LAYOUT}\n{sys.version}\n{BUNDLE_RESOURCES}\n"


def _venv_is_current() -> bool:
    """True when the user's environment was built by this exact bundle.
    An app update moves the bundled interpreter, which leaves the old
    environment pointing at a runtime that no longer exists."""
    python = VENV / "bin" / "python3"
    if not python.exists():
        return False
    try:
        return STAMP.read_text(encoding="utf-8") == _stamp_value()
    except OSError:
        return False


def _build_venv() -> None:
    import shutil
    import venv

    if VENV.exists():
        shutil.rmtree(VENV, ignore_errors=True)
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    # with_pip: the Models tab shells out to pip to add engines.
    # system_site_packages: everything shipped in the bundle stays
    # importable without being copied.
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=True,
                              symlinks=True)
    builder.create(VENV)
    STAMP.write_text(_stamp_value(), encoding="utf-8")


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not _venv_is_current():
        try:
            _build_venv()
        except Exception as exc:
            _alert(
                "Transcribr could not finish setting itself up for this "
                "user.\n\n%s: %s\n\nThe folder it needs is:\n%s"
                % (type(exc).__name__, exc, APP_SUPPORT)
            )
            return 1

    python = VENV / "bin" / "python3"
    script = BUNDLE_RESOURCES / "transcribr.py"
    if not script.exists():
        _alert("Transcribr's application file is missing from the app "
               "bundle. Reinstall Transcribr.")
        return 1

    # Hand over the process entirely - the app owns the run from here.
    os.execv(str(python), [str(python), str(script), *sys.argv[1:]])
    return 1        # execv only returns on failure


if __name__ == "__main__":
    sys.exit(main())
