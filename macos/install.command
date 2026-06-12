#!/bin/bash
# Transcribr - Installer
#
# Installs Transcribr and all of its dependencies on macOS.
# Safe to re-run: skips steps that are already done.
#
# What this installs:
#   - Homebrew (if missing)
#   - Python 3.12 (via Homebrew)
#   - ffmpeg (via Homebrew)
#   - python-tk@3.12 (Tk bindings for Python 3.12)
#   - A Python virtualenv at ~/Library/Application Support/Transcribr/venv
#   - Three Whisper engines inside that venv:
#       * openai-whisper (reference; needed on all systems)
#       * faster-whisper (CTranslate2-based; ~4x faster on CPU)
#       * mlx-whisper    (Apple Silicon only; uses the Mac's GPU/ANE)
#   - /Applications/Transcribr.app (a thin launcher)
#
# Total disk usage: ~500MB - 2GB depending on what is already installed
# (PyTorch, which Whisper needs, is the bulk of it).
#
# What this does NOT install:
#   - Whisper model weights. The first time you run a particular model,
#     Whisper downloads it (~150MB for small.en, ~1.5GB for medium.en) and
#     caches it in ~/.cache/whisper/.

set -uo pipefail   # fail on undefined vars and pipe errors, but not on
                   # individual command errors - we handle those ourselves

# ---------- pretty output --------------------------------------------------

if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'
    RESET=$'\033[0m'
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step()    { echo; echo "${BOLD}==> $*${RESET}"; }
info()    { echo "    $*"; }
ok()      { echo "    ${GREEN}OK:${RESET} $*"; }
warn()    { echo "    ${YELLOW}WARNING:${RESET} $*"; }
fail()    { echo; echo "${RED}${BOLD}ERROR:${RESET} $*" >&2; exit 1; }

confirm() {
    # confirm "Question?" - returns 0 (yes) or 1 (no)
    local prompt="$1"
    local reply
    while true; do
        read -r -p "    $prompt [y/n] " reply
        case "$reply" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "    Please answer y or n." ;;
        esac
    done
}

# ---------- preflight ------------------------------------------------------

INSTALLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="$(cd "$INSTALLER_DIR/.." && pwd)"

step "Transcribr installer"
info "Source: $INSTALLER_DIR"
info "macOS:  $(sw_vers -productVersion 2>/dev/null || echo unknown)"
info "Arch:   $(uname -m)"
echo
info "This installer will install Homebrew (if missing), Python 3.12,"
info "ffmpeg, and create a Python environment containing Whisper. It will"
info "also create /Applications/Transcribr.app."
info
info "It is safe to re-run."
echo
if ! confirm "Continue?"; then
    info "Aborted."
    exit 0
fi

# Verify the files we need are alongside the installer.
[ -e "$SHARED_DIR/transcribr.py" ] || \
    fail "Missing file: ../transcribr.py (expected one folder up)"
for f in app_template/launcher app_template/Info.plist; do
    [ -e "$INSTALLER_DIR/$f" ] || fail "Missing file: $f (expected next to install.command)"
done

# ---------- Homebrew -------------------------------------------------------

step "Step 1/6: Homebrew"

# Determine the standard Homebrew prefix for this CPU.
if [ "$(uname -m)" = "arm64" ]; then
    BREW_PREFIX="/opt/homebrew"
else
    BREW_PREFIX="/usr/local"
fi
BREW_BIN="$BREW_PREFIX/bin/brew"

if [ -x "$BREW_BIN" ]; then
    ok "Homebrew already installed at $BREW_PREFIX"
else
    info "Homebrew is not installed."
    info "The official installer will run; it may ask for your password"
    info "in order to create directories under $BREW_PREFIX."
    if ! confirm "Install Homebrew now?"; then
        fail "Homebrew is required. Aborting."
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail "Homebrew installation failed."
    [ -x "$BREW_BIN" ] || fail "Homebrew was installed but $BREW_BIN is missing."
    ok "Homebrew installed."
fi

# Make brew available for the rest of this script regardless of shell config.
eval "$("$BREW_BIN" shellenv)"

# ---------- Homebrew packages ---------------------------------------------

step "Step 2/6: Python 3.12, ffmpeg, python-tk@3.12"

install_brew_pkg() {
    local pkg="$1"
    if brew list --formula "$pkg" >/dev/null 2>&1; then
        ok "$pkg already installed"
    else
        info "Installing $pkg..."
        brew install "$pkg" || fail "Failed to install $pkg"
        ok "$pkg installed"
    fi
}

install_brew_pkg python@3.12
install_brew_pkg ffmpeg
install_brew_pkg python-tk@3.12

# Resolve the actual python3.12 binary we just installed.
PYTHON312="$(brew --prefix python@3.12)/bin/python3.12"
[ -x "$PYTHON312" ] || fail "python3.12 not found at $PYTHON312"
info "Using: $PYTHON312"

# ---------- venv -----------------------------------------------------------

step "Step 3/6: Python virtual environment"

APP_SUPPORT="$HOME/Library/Application Support/Transcribr"
VENV="$APP_SUPPORT/venv"
mkdir -p "$APP_SUPPORT"

if [ -f "$VENV/bin/activate" ]; then
    info "Existing venv found at: $VENV"
    if confirm "Recreate it from scratch (slower but cleanest)?"; then
        rm -rf "$VENV"
    fi
fi

if [ ! -f "$VENV/bin/activate" ]; then
    info "Creating venv at: $VENV"
    "$PYTHON312" -m venv "$VENV" || fail "venv creation failed"
fi

# Sanity: tkinter must work in this venv (catches a misinstalled python-tk).
"$VENV/bin/python" -c "import tkinter; tkinter.Tk().destroy()" 2>/dev/null \
    || warn "tkinter test failed - the GUI may not start. Try:
        brew reinstall python-tk@3.12"

ok "venv ready"

# ---------- Whisper --------------------------------------------------------

step "Step 4/6: Whisper engines (this is the slow step)"

info "Upgrading pip..."
"$VENV/bin/pip" install --upgrade pip --quiet \
    || fail "pip upgrade failed"

# Pin openai-whisper >= 20250625; older releases use the removed
# pkg_resources module, which is gone in setuptools 81+.
#
# On macOS 15 Sequoia, some Homebrew Python 3.12 builds set
# MACOSX_DEPLOYMENT_TARGET="15" (without the required minor version).
# Apple's clang rejects this with: "invalid version number in
# 'MACOSX_DEPLOYMENT_TARGET=15'", which then makes pip fall back to
# source-building numba and llvmlite (which need LLVM tooling that isn't
# present and can't easily be installed). We override the variable to a
# well-formed older value, which both lets clang accept it AND nudges pip
# toward downloading the available pre-built wheels rather than building
# from source.
export MACOSX_DEPLOYMENT_TARGET=11.0

# Intel Macs are stuck on torch 2.2.2 because PyTorch stopped publishing
# x86_64 macOS wheels after that release (Jan 2024). Torch 2.2.2 predates
# NumPy 2.0 and cannot bridge to it -- torch.from_numpy() raises
# "RuntimeError: Numpy is not available" with NumPy 2.x. So on Intel Macs
# we have to pin the whole chain backwards: torch 2.2.2, numpy 1.x, and
# numba 0.59.x (the last numba line that supports numpy 1.x with cp312
# wheels).
#
# On Apple Silicon (arm64), the latest torch ships fine and the whole
# stack works with numpy 2.x. So we only apply the constraints on Intel.
ARCH=$("$VENV/bin/python" -c "import platform; print(platform.machine())")

info "Installing openai-whisper (downloads PyTorch, ~1.5GB)..."
if [ "$ARCH" = "x86_64" ]; then
    info "Intel Mac detected; pinning torch/numpy/numba versions for compatibility"
    "$VENV/bin/pip" install --upgrade --prefer-binary \
        "openai-whisper>=20250625" python-docx reportlab \
        "torch==2.2.2" "numpy<2" "numba<0.60" \
        || fail "openai-whisper / python-docx / reportlab install failed"
else
    "$VENV/bin/pip" install --upgrade --prefer-binary \
        "openai-whisper>=20250625" python-docx reportlab \
        || fail "openai-whisper / python-docx / reportlab install failed"
fi

# Verify whisper imports cleanly.
"$VENV/bin/python" -c "import whisper; print('    whisper version:', whisper.__version__ if hasattr(whisper, '__version__') else 'OK')" \
    || fail "whisper import test failed"
ok "openai-whisper installed"

# faster-whisper - CTranslate2-based engine, ~4x faster on CPU than the
# reference. Cross-platform via ctranslate2 wheels.
info "Installing faster-whisper..."
"$VENV/bin/pip" install --upgrade --prefer-binary faster-whisper \
    || warn "faster-whisper install failed - the app will run, but only \
the OpenAI engine will be available."

"$VENV/bin/python" -c "import faster_whisper" 2>/dev/null \
    && ok "faster-whisper installed" \
    || warn "faster-whisper import check failed; it will not be offered \
in the app."

# mlx-whisper - Apple's MLX framework. Apple Silicon only, requires
# macOS 13.5+. The Python package depends on `mlx`, which is what
# actually talks to the GPU/Neural Engine. We install both, then verify
# both import. If either fails to import we uninstall the dist-info so
# the app doesn't offer mlx in its engine dropdown (Transcribr probes
# packages via find_spec, which would otherwise see broken metadata and
# show mlx as available).
if [ "$ARCH" = "arm64" ]; then
    MACOS_VERSION=$(sw_vers -productVersion)
    MACOS_MAJOR=$(echo "$MACOS_VERSION" | cut -d. -f1)
    MACOS_MINOR=$(echo "$MACOS_VERSION" | cut -d. -f2)
    MACOS_MINOR=${MACOS_MINOR:-0}
    # mlx refuses to import on macOS < 13.5. Skip cleanly, AND scrub any
    # existing broken install so the app's Engine dropdown stops
    # offering it (find_spec sees the dist-info but the import would
    # raise on this OS).
    if [ "$MACOS_MAJOR" -lt 13 ] \
       || { [ "$MACOS_MAJOR" -eq 13 ] && [ "$MACOS_MINOR" -lt 5 ]; }; then
        warn "macOS version is $MACOS_VERSION; mlx requires 13.5+. \
Skipping mlx install."
        if "$VENV/bin/pip" show mlx >/dev/null 2>&1 \
           || "$VENV/bin/pip" show mlx-whisper >/dev/null 2>&1; then
            info "Removing previously-installed mlx / mlx-whisper so \
the app stops offering it."
            "$VENV/bin/pip" uninstall -y mlx-whisper mlx \
                >/dev/null 2>&1 || true
        fi
    else
        info "Installing mlx + mlx-whisper (Apple Silicon GPU/Neural \
Engine)..."
        if "$VENV/bin/pip" install --upgrade --prefer-binary mlx mlx-whisper; then
            if "$VENV/bin/python" -c "import mlx_whisper" 2>/dev/null \
               && "$VENV/bin/python" -c "import mlx" 2>/dev/null; then
                ok "mlx-whisper installed"
            else
                warn "mlx or mlx-whisper imports failed after install."
                info "Removing broken install so the app doesn't offer it."
                "$VENV/bin/pip" uninstall -y mlx-whisper mlx >/dev/null 2>&1 || true
            fi
        else
            warn "mlx-whisper install failed - the app will run without it."
        fi
    fi
else
    info "Intel Mac: skipping mlx-whisper (Apple Silicon only)."
fi

# ---------- copy GUI script ------------------------------------------------

step "Step 5/6: Application files"

cp "$SHARED_DIR/transcribr.py" "$APP_SUPPORT/transcribr.py"
ok "Copied transcribr.py to $APP_SUPPORT"

if [ -f "$SHARED_DIR/icon.png" ]; then
    cp "$SHARED_DIR/icon.png" "$APP_SUPPORT/icon.png"
    ok "Copied icon.png to $APP_SUPPORT"
fi

if [ -f "$SHARED_DIR/README.md" ]; then
    cp "$SHARED_DIR/README.md" "$APP_SUPPORT/README.md"
    ok "Copied README.md to $APP_SUPPORT"
fi

# ---------- build .app bundle ---------------------------------------------

step "Step 6/6: Transcribr.app"

APP="/Applications/Transcribr.app"

if [ -e "$APP" ]; then
    info "Existing app at: $APP"
    rm -rf "$APP" || fail "Could not remove existing app (permission denied?)"
fi

mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# Render the launcher template, substituting the venv and script paths.
sed \
    -e "s|@VENV@|$VENV|g" \
    -e "s|@SCRIPT@|$APP_SUPPORT/transcribr.py|g" \
    "$INSTALLER_DIR/app_template/launcher" \
    > "$APP/Contents/MacOS/launcher"
chmod +x "$APP/Contents/MacOS/launcher"

cp "$INSTALLER_DIR/app_template/Info.plist" "$APP/Contents/Info.plist"

# Optional icon (only included if the installer ships one).
if [ -f "$INSTALLER_DIR/app_template/icon.icns" ]; then
    cp "$INSTALLER_DIR/app_template/icon.icns" "$APP/Contents/Resources/icon.icns"
fi

# Tell macOS to refresh its app database so Spotlight/Launchpad see it.
/usr/bin/touch "$APP"

ok "Installed to: $APP"

# ---------- finished -------------------------------------------------------

step "Done!"
info "Launch ${BOLD}Transcribr${RESET} from:"
info "  - Spotlight (cmd+space, type 'whisper')"
info "  - Launchpad"
info "  - the Applications folder"
info
info "${YELLOW}First launch only:${RESET} macOS may say the app is from an"
info "unidentified developer. Right-click the app, choose ${BOLD}Open${RESET},"
info "then ${BOLD}Open${RESET} again in the dialog. Subsequent launches will"
info "work normally."
info
info "If anything misbehaves, run this installer again - it is safe to repeat."
echo
