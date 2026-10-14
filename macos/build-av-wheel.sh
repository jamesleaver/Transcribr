#!/usr/bin/env bash
# Build the LGPL, audio-only PyAV wheel for one macOS architecture:
# FFmpeg from build-ffmpeg.sh, PyAV compiled against it, its libraries
# folded into the wheel by delocate, then checked.
#
#   ARCH=arm64  macos/build-av-wheel.sh
#   ARCH=x86_64 macos/build-av-wheel.sh     (on Apple Silicon: via Rosetta)
#
# Everything lands under $OUT (default ~/Library/Developer/
# Transcribr-build: no spaces, which LAME's libtool cannot take) -
# outside Dropbox, which makes files online-only, and outside /tmp,
# where a reboot can lose a finished wheel. The
# finished wheel is $OUT/wheels/av-*.whl.
#
# The Python is the python-build-standalone 3.12 that build-pkg.sh
# bundles (build/macos-$ARCH/python.tar.gz), so the wheel's tags match
# the interpreter that will load it.
set -euo pipefail

ARCH="${ARCH:-$(uname -m)}"
AV_VERSION="${AV_VERSION:-19.0.0}"
OUT="${OUT:-$HOME/Library/Developer/Transcribr-build}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"

case "$ARCH" in arm64|x86_64) ;; *) echo "ARCH must be arm64 or x86_64" >&2; exit 2 ;; esac

# An Intel build on Apple Silicon runs the whole thing under Rosetta, so
# every compiler, configure test and Python sees an x86_64 machine.
if [ "$(uname -m)" != "$ARCH" ]; then
    exec arch "-$ARCH" /usr/bin/env ARCH="$ARCH" AV_VERSION="$AV_VERSION" \
        OUT="$OUT" PATH="$PATH" HOME="$HOME" /bin/bash "$0" "$@"
fi

# Even under Rosetta, Apple's clang builds for the machine it runs on
# unless told otherwise - x86 code then meets an arm64 compiler. Name the
# target for everything that compiles: LAME, FFmpeg, and PyAV's setup.
if [ "$ARCH" = x86_64 ]; then
    export CC="clang -arch x86_64" CXX="clang++ -arch x86_64"
    export ARCHFLAGS="-arch x86_64"
fi

root="$OUT/$ARCH"
mkdir -p "$root" "$OUT/wheels"
log() { echo "[$(date '+%H:%M:%S') $ARCH] $*"; }

# ---- Python ----------------------------------------------------------
pytar="$repo/build/macos-$ARCH/python.tar.gz"
PY="$root/python/bin/python3.12"
if [ ! -x "$PY" ]; then
    [ -f "$pytar" ] || { echo "missing $pytar - run build-pkg.sh once to fetch it" >&2; exit 1; }
    log "unpacking the bundled Python"
    tar -xzf "$pytar" -C "$root"
fi
"$PY" -c "import platform,sys; assert platform.machine()=='$ARCH', platform.machine(); print(sys.version)"

# ---- FFmpeg ----------------------------------------------------------
extra=""
if [ "$ARCH" = x86_64 ] && ! command -v nasm >/dev/null; then
    extra="--disable-x86asm"
fi
if [ ! -f "$root/ffmpeg/lib/libavcodec.dylib" ]; then
    log "building FFmpeg"
    PREFIX="$root/ffmpeg" EXTRA_CONFIGURE="$extra" bash "$here/build-ffmpeg.sh"
fi

# ---- PyAV --------------------------------------------------------------
venv="$root/venv"
[ -x "$venv/bin/python" ] || "$PY" -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --upgrade pip delocate
rm -rf "$root/dist"
log "building PyAV $AV_VERSION against it"
PKG_CONFIG_PATH="$root/ffmpeg/lib/pkgconfig" \
    "$venv/bin/python" -m pip wheel --no-binary av --no-deps \
    "av==$AV_VERSION" -w "$root/dist" > "$root/pyav.log" 2>&1 \
    || { tail -30 "$root/pyav.log" >&2; exit 1; }
raw="$(ls "$root"/dist/av-*.whl)"

# Fold the FFmpeg libraries into the wheel, then check what came along.
rm -rf "$root/fixed"
DYLD_LIBRARY_PATH="$root/ffmpeg/lib" "$venv/bin/delocate-wheel" \
    -w "$root/fixed" "$raw" > "$root/delocate.log" 2>&1 \
    || { tail -30 "$root/delocate.log" >&2; exit 1; }
wheel="$(ls "$root"/fixed/av-*.whl)"
deps="$("$venv/bin/delocate-listdeps" --all "$wheel")"
FORBIDDEN='libx264|libx265|libvpx|libdav1d|libSvtAv1|libwebp|libopus|libopencore|libX11|libxcb|/opt/homebrew|/usr/local/'
if echo "$deps" | grep -E "$FORBIDDEN" >&2; then
    echo "REFUSING: the wheel carries a forbidden or host library (above)" >&2
    exit 1
fi

# ---- Does it work? ---------------------------------------------------
log "checking the wheel"
check="$root/check"
rm -rf "$check" && "$PY" -m venv "$check"
"$check/bin/python" -m pip install --quiet "$wheel"
"$check/bin/python" "$repo/tools/check_av_wheel.py"

cp "$wheel" "$OUT/wheels/"
log "WHEEL OK -> $OUT/wheels/$(basename "$wheel")"
du -h "$wheel" | cut -f1
