"""Checking GitHub for a newer release."""

from pathlib import Path
import os
import re
import subprocess
import sys
import threading
import traceback

from .version import __version__
from .errors import ApiFail
from .platformutil import _log


#
# Transcribr ships as a GitHub release: a small installer zip that the
# platform installer script unpacks. The app asks GitHub once per
# launch whether a newer release exists, and can fetch that zip and
# hand it to the installer. Nothing about the user's files, transcripts
# or usage is sent - it is an unauthenticated GET of the public
# releases API - but because the app otherwise never touches the
# network unprompted, the check is a setting the user can turn off.

_UPDATE_REPO = "jamesleaver/Transcribr"
_UPDATE_API = f"https://api.github.com/repos/{_UPDATE_REPO}/releases/latest"
_UPDATE_RELEASES_URL = f"https://github.com/{_UPDATE_REPO}/releases/latest"


def _version_tuple(text):
    """("0.9.12" or "v0.9.12") -> ((0, 9, 12), 1), an ordering key.

    The trailing flag is 0 for a pre-release (1.0.0-rc1) and 1 for the
    plain release, so 1.0.0 sorts above 1.0.0-rc1. Numbers are padded
    to three components so "1.0" and "1.0.0" compare equal."""
    cleaned = str(text or "").strip().lstrip("vV")
    parts = []
    final = 1
    for chunk in re.split(r"[.\-+]", cleaned):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            final = 0               # rc / beta / dev suffix
            break
    while len(parts) < 3:
        parts.append(0)
    return (tuple(parts), final)


def _is_newer(candidate, current=None):
    return _version_tuple(candidate) > _version_tuple(current or __version__)


class UpdateChecker:
    """Asks GitHub for the newest release, and (on request) downloads
    that release's installer zip and opens it for the user.

    States: idle -> checking -> current | available | error, and the
    install side runs available -> downloading -> ready_to_install.
    """

    def __init__(self, broker):
        self.broker = broker
        self.lock = threading.RLock()
        self.state = "idle"
        self.latest = None          # version string of the newest release
        self.notes = ""
        self.url = _UPDATE_RELEASES_URL
        self.asset = None           # (name, url, size, sha256|None)
        self.error = None
        self.pct = 0
        self.installer_path = None

    # -- status ---------------------------------------------------------

    def status(self):
        with self.lock:
            return {
                "state": self.state,
                "current": __version__,
                "latest": self.latest,
                "notes": self.notes,
                "url": self.url,
                "pct": self.pct,
                "error": self.error,
                "can_install": bool(self.asset) and bool(_ASSET_PREFERENCE),
            }

    def _set(self, state, **kw):
        with self.lock:
            self.state = state
            for k, v in kw.items():
                setattr(self, k, v)
        self.broker.publish("update", self.status())

    # -- checking -------------------------------------------------------

    def check_async(self):
        """Kick off a check unless one is already running."""
        with self.lock:
            if self.state in ("checking", "downloading"):
                return
        threading.Thread(target=self.check, daemon=True,
                         name="transcribr-update").start()

    def check(self):
        import json
        import urllib.request
        self._set("checking", error=None)
        try:
            req = urllib.request.Request(
                _UPDATE_API,
                headers={"User-Agent": f"Transcribr/{__version__}",
                         "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self._set("error",
                      error=f"Couldn't reach GitHub ({type(e).__name__}).")
            return
        tag = data.get("tag_name") or data.get("name") or ""
        notes = (data.get("body") or "").strip()
        html = data.get("html_url") or _UPDATE_RELEASES_URL
        asset = None
        available = [a for a in (data.get("assets") or [])
                     if a.get("browser_download_url") and a.get("name")]
        mine = _this_machine_tokens()
        for suffix in _ASSET_PREFERENCE:
            matching = [a for a in available
                        if a["name"].lower().endswith(suffix)]
            if not matching:
                continue
            # When a suffix has architecture-specific builds, take ours
            # or none - never fall back to a package for another
            # architecture, which would install an app that won't run.
            tokened = [a for a in matching
                       if any(t in a["name"].lower()
                              for t in _ALL_ARCH_TOKENS)]
            if tokened:
                ours = [a for a in tokened
                        if any(t in a["name"].lower() for t in mine)]
                if not ours:
                    continue
                chosen = ours[0]
            else:
                chosen = matching[0]
            digest = chosen.get("digest") or ""
            asset = (chosen["name"], chosen["browser_download_url"],
                     int(chosen.get("size") or 0),
                     digest.split("sha256:")[-1] if "sha256:" in digest
                     else None)
            break
        with self.lock:
            self.latest = tag.lstrip("vV") or None
            self.notes = notes
            self.url = html
            self.asset = asset
        if self.latest and _is_newer(self.latest):
            self._set("available")
        else:
            self._set("current")

    # -- fetching the installer ------------------------------------------

    def install_async(self):
        with self.lock:
            if self.state == "downloading":
                raise ApiFail(409, "busy", "An update is already downloading.")
            if not self.asset:
                raise ApiFail(409, "no_asset",
                              "This release has no installer to download.")
            if not _ASSET_PREFERENCE:
                raise ApiFail(409, "unsupported",
                              "Automatic install isn't supported on this "
                              "platform - use the Downloads page.")
        threading.Thread(target=self._install, daemon=True,
                         name="transcribr-update-install").start()

    def _install(self):
        import hashlib
        import tempfile
        import urllib.request
        import zipfile
        with self.lock:
            name, url, size, sha = self.asset
        self._set("downloading", pct=0, error=None)
        try:
            # What arrives here gets handed to the operating system and
            # run. Two things are checked before a byte is fetched,
            # because afterwards is too late.
            #
            # A digest is required rather than used when present. Every
            # release carries one, so its absence means something is
            # wrong rather than something is old - and "if sha and ..."
            # quietly turned the only integrity check off in exactly the
            # case where it mattered most. It also stands in for
            # checking the download completed: a connection dropped
            # mid-file leaves a short one, and read() returning empty
            # looks the same as finishing.
            if not sha:
                raise RuntimeError(
                    "this release does not publish a checksum, so the "
                    "download cannot be verified - install it by hand "
                    "from the releases page instead")
            if not _is_github_url(url):
                raise RuntimeError(
                    f"the download address for this release is not a "
                    f"GitHub one ({url.split('/')[2] if '//' in url else url})"
                    " - install it by hand from the releases page instead")
            dest = Path(tempfile.mkdtemp(prefix="transcribr-update-"))
            zip_path = dest / name
            digest = hashlib.sha256()
            req = urllib.request.Request(
                url, headers={"User-Agent": f"Transcribr/{__version__}"})
            with urllib.request.urlopen(req, timeout=120) as resp, \
                    open(zip_path, "wb") as out:
                total = int(resp.headers.get("Content-Length") or size or 0)
                done = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if total:
                        pct = min(99, int(done * 100 / total))
                        with self.lock:
                            changed = pct != self.pct
                            self.pct = pct
                        if changed:
                            self.broker.publish("update", self.status())
            if digest.hexdigest().lower() != sha.lower():
                raise RuntimeError(
                    "the download failed its integrity check")

            if zip_path.suffix.lower() == ".zip":
                # Source archive: unpack and run the platform installer
                # script inside it.
                with zipfile.ZipFile(zip_path) as z:
                    _safe_extract_zip(z, dest)
                target = None
                for candidate in dest.rglob(_PLATFORM_INSTALLER or "*"):
                    target = candidate
                    break
                if target is None:
                    raise RuntimeError(
                        f"the download didn't contain {_PLATFORM_INSTALLER}")
                os.chmod(target, 0o755)
            else:
                # A .pkg or .exe is the installer; hand it over as-is.
                target = zip_path

            self._open_installer(target)
            self._set("ready_to_install", pct=100,
                      installer_path=str(target))
        except Exception as e:
            _log("update install failed:\n" + traceback.format_exc())
            detail = str(e).strip() or type(e).__name__
            self._set("error", error=f"Update failed: {detail}.")

    @staticmethod
    def _open_installer(target):
        """Hand the installer to the OS so the user watches it run and
        can answer its prompts - a .pkg asks for confirmation, and the
        script installer may want their password for Homebrew. Running
        it silently in the background would leave those invisible."""
        target = Path(target)
        if sys.platform == "darwin":
            if target.suffix.lower() == ".pkg":
                # Opens in Installer.app - the ordinary macOS flow.
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["open", "-a", "Terminal", str(target)])
        elif os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", str(target)],
                             cwd=str(target.parent))
        else:
            raise RuntimeError("unsupported platform")


_PLATFORM_INSTALLER = ("install.command" if sys.platform == "darwin"
                       else "install.bat" if os.name == "nt" else None)

# Which release asset to fetch, best first. The signed .pkg and the
# Windows .exe are real installers and open directly; the source .zip is
# the fallback, used for releases published before those existed and on
# Intel Macs, where no .pkg is built.
if sys.platform == "darwin":
    _ASSET_PREFERENCE = (".pkg", ".zip")
elif os.name == "nt":
    _ASSET_PREFERENCE = (".exe", ".zip")
else:
    _ASSET_PREFERENCE = ()

# Architecture tokens that can appear in an asset's name. macOS releases
# carry an arm64 and an x86_64 package side by side, and handing someone
# the wrong one installs an app that cannot launch.
_ARCH_TOKENS = {
    "arm64": ("arm64", "aarch64", "apple-silicon"),
    "x86_64": ("x86_64", "x86-64", "amd64", "intel"),
}
_ALL_ARCH_TOKENS = tuple(t for group in _ARCH_TOKENS.values() for t in group)


def _this_machine_tokens():
    """Asset-name tokens that mean 'built for this machine'."""
    import platform
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return _ARCH_TOKENS["arm64"]
    if machine in ("x86_64", "amd64"):
        return _ARCH_TOKENS["x86_64"]
    return ()


# Where a release asset may legitimately be served from. The address
# comes out of the GitHub API over TLS, so this is not the load-bearing
# check - it is there so that a release whose asset URL points somewhere
# else stops here rather than at the point of running it.
_GITHUB_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)


def _is_github_url(url):
    """True when `url` is https and served by GitHub."""
    from urllib.parse import urlparse
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h)
               for h in _GITHUB_DOWNLOAD_HOSTS)


def _safe_extract_zip(zf, dest):
    """Extract `zf` under `dest`, refusing entries that would escape it
    (absolute paths or ../ traversal) - the archive comes off the
    network, so it doesn't get to choose where it lands."""
    root = Path(dest).resolve()
    for member in zf.infolist():
        target = (root / member.filename).resolve()
        if not str(target).startswith(str(root) + os.sep) and target != root:
            raise RuntimeError("the archive contained an unsafe path")
    zf.extractall(dest)
