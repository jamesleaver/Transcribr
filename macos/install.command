#!/bin/bash
# Transcribr - Installer
#
# Installs Transcribr and all of its dependencies on macOS.
# Safe to re-run: skips steps that are already done.
#
# What this installs:
#   - Homebrew (if missing)
#   - Python 3.12 (via Homebrew)
#   - A Python virtualenv at ~/Library/Application Support/Transcribr/venv
#   - The default Whisper engine(s) inside that venv:
#       * faster-whisper (CTranslate2-based; ~4x faster on CPU, no
#         PyTorch. Its PyAV dependency bundles FFmpeg's libraries, so
#         no separate ffmpeg install is needed either)
#       * mlx-whisper    (Apple Silicon only; uses the Mac's GPU/ANE)
#       * sherpa-onnx    (the optional "detect speakers" feature)
#   - /Applications/Transcribr.app (a thin launcher)
#
# The reference openai-whisper engine (which pulls in PyTorch, ~2 GB) is
# NOT installed up front - it's optional and can be added later from the
# app's Models tab. Base install is a few hundred MB.
#
# What this does NOT install:
#   - Whisper model weights. The first time you run a particular model,
#     it is downloaded (~150MB for small, ~1.5GB for large) and cached
#     (faster-whisper / mlx-whisper: ~/.cache/huggingface/hub).

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
info "This installer will install Homebrew (if missing) and Python 3.12,"
info "and create a Python environment containing Whisper. It will"
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

step "Step 2/6: Python 3.12"

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
# (ffmpeg is no longer needed: audio decoding and playback go through
# the PyAV package, whose wheel bundles FFmpeg's libraries.)


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

ok "venv ready"

# ---------- App libraries + Whisper engine ---------------------------------

step "Step 4/6: App libraries and the faster-whisper engine"

info "Upgrading pip..."
"$VENV/bin/pip" install --upgrade pip --quiet \
    || fail "pip upgrade failed"

# Some Homebrew Python 3.12 builds on macOS 15 set
# MACOSX_DEPLOYMENT_TARGET="15" (without the required minor version),
# which Apple's clang rejects ("invalid version number"), forcing pip to
# source-build wheels. Override with a well-formed value so pip prefers
# the pre-built wheels.
export MACOSX_DEPLOYMENT_TARGET=11.0

ARCH=$("$VENV/bin/python" -c "import platform; print(platform.machine())")

# Base libraries plus faster-whisper - the default engine. It's
# CTranslate2-based (no PyTorch), a few hundred MB, and ~4x faster on CPU
# than the reference OpenAI engine with essentially identical output. The
# heavier OpenAI engine is optional and installable later from the app's
# Models tab, so we no longer download PyTorch (~2 GB) up front.
# sherpa-onnx powers "Detect speakers automatically" (~20 MB; its two
# small voice models download on first use from inside the app).
info "Installing app libraries + faster-whisper..."
"$VENV/bin/pip" install --upgrade --prefer-binary \
    faster-whisper sherpa-onnx python-docx reportlab \
    pyobjc-framework-Cocoa pywebview bottle \
    || fail "core install failed (faster-whisper / python-docx / reportlab \
/ pywebview / bottle)"

# The web interface is served by bottle inside a pywebview window; verify
# both import.
"$VENV/bin/python" -c "import webview, bottle" \
    || fail "pywebview / bottle import test failed"
ok "pywebview + bottle installed"

"$VENV/bin/python" -c "import faster_whisper, av" 2>/dev/null \
    && ok "faster-whisper installed (PyAV handles audio decoding)" \
    || fail "faster-whisper import check failed - no engine would be \
available."

"$VENV/bin/python" -c "import sherpa_onnx" 2>/dev/null \
    && ok "sherpa-onnx installed (speaker detection available)" \
    || warn "sherpa-onnx import check failed - the app will run, but \
'Detect speakers automatically' will be unavailable."

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

# The built web interface. Shipped pre-built in webdist/ - end users
# never need Node.
if [ -e "$SHARED_DIR/webdist/index.html" ]; then
    rm -rf "$APP_SUPPORT/webdist"
    cp -R "$SHARED_DIR/webdist" "$APP_SUPPORT/webdist"
    ok "Copied web interface (webdist) to $APP_SUPPORT"
else
    fail "webdist/index.html is missing - the web interface has not \
been built. From the repository: cd web && npm install && npm run build"
fi

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
