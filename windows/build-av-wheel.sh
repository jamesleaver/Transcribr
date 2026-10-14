#!/usr/bin/env bash
# The Windows twin of macos/build-av-wheel.sh: an LGPL, audio-only
# FFmpeg built with MSVC, PyAV compiled against it, and its DLLs folded
# into the wheel. Runs in an MSYS2 shell that has inherited a Visual
# Studio x64 developer environment (cl.exe, link.exe, nmake on PATH) -
# see .github/workflows/build-av-wheel.yml, which is the only place it
# is expected to run.
#
#   PY=/c/hostedtoolcache/.../python.exe OUT=/c/avbuild windows/build-av-wheel.sh
#
# The configure flags mirror macos/build-ffmpeg.sh; keep the two in step.
set -euo pipefail

FFMPEG_VERSION="${FFMPEG_VERSION:-8.1.2}"
LAME_VERSION="${LAME_VERSION:-3.100}"
AV_VERSION="${AV_VERSION:-19.0.0}"
PY="${PY:?set PY to the Python 3.12 that will build the wheel}"
OUT="${OUT:?set OUT to a build directory}"
here="$(cd "$(dirname "$0")" && pwd)"

src="$OUT/src"; prefix="$OUT/ffmpeg"
mkdir -p "$src" "$prefix/include/lame" "$prefix/lib" "$prefix/bin" "$OUT/wheels"
cd "$src"
log() { echo "[$(date '+%H:%M:%S') win64] $*"; }

fetch() {   # url file test-command
    [ -f "$2" ] && $3 "$2" >/dev/null 2>&1 && return 0
    curl -fL --retry 5 --retry-all-errors -o "$2.part" "$1"
    $3 "$2.part" >/dev/null || { echo "corrupt download: $2" >&2; exit 1; }
    mv "$2.part" "$2"
}
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" \
    "ffmpeg-$FFMPEG_VERSION.tar.xz" "tar -tf"
fetch "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz" \
    "lame-$LAME_VERSION.tar.gz" "tar -tzf"

# ---- LAME, static, with its own MSVC makefile -------------------------
if [ ! -f "$prefix/lib/mp3lame.lib" ]; then
    log "building LAME"
    rm -rf "lame-$LAME_VERSION" && tar -xzf "lame-$LAME_VERSION.tar.gz"
    (
        cd "lame-$LAME_VERSION"
        cp configMS.h config.h
        # MSYS would rewrite "/f" as a path; MSYS2_ARG_CONV_EXCL stops it.
        MSYS2_ARG_CONV_EXCL='*' nmake -nologo -f Makefile.MSVC \
            comp=msvc asm=no MACHINE=/machine:x64 libmp3lame-static.lib \
            > "$OUT/lame.log" 2>&1
    ) || { tail -40 "$OUT/lame.log" >&2; exit 1; }
    cp "lame-$LAME_VERSION/output/libmp3lame-static.lib" "$prefix/lib/mp3lame.lib"
    cp "lame-$LAME_VERSION/include/lame.h" "$prefix/include/lame/lame.h"
fi

# ---- FFmpeg -------------------------------------------------------------
if [ ! -f "$prefix/bin/avcodec.lib" ] && [ ! -f "$prefix/lib/avcodec.lib" ]; then
    log "configuring FFmpeg"
    rm -rf "ffmpeg-$FFMPEG_VERSION" && tar -xf "ffmpeg-$FFMPEG_VERSION.tar.xz"
    cd "ffmpeg-$FFMPEG_VERSION"
    conf_log="$OUT/configure-output.log"
    ./configure \
        --prefix="$prefix" \
        --toolchain=msvc --target-os=win64 --arch=x86_64 \
        --disable-static --enable-shared \
        --disable-programs --disable-doc \
        --enable-version3 \
        --enable-libmp3lame \
        --extra-cflags="-I$(cygpath -m "$prefix/include")" \
        --extra-ldflags="-LIBPATH:$(cygpath -m "$prefix/lib")" \
        --disable-libxml2 --disable-lzma --disable-libtheora --disable-libfreetype \
        --disable-libfontconfig --disable-libbluray --disable-libopenjpeg \
        --disable-gnutls --disable-schannel --disable-libjack --disable-indev=jack \
        --disable-mediafoundation --disable-d3d11va --disable-d3d12va \
        --disable-dxva2 --disable-amf --disable-cuda --disable-cuvid \
        --disable-nvenc --disable-nvdec --disable-ffnvcodec \
        --disable-sdl2 --disable-zlib --disable-bzlib --disable-iconv \
        --disable-encoder='avui,dca,mlp,opus,s302m,sonic,sonic_ls,truehd' \
        --disable-decoder=sonic \
        > "$conf_log" 2>&1 || { tail -40 "$conf_log"; tail -60 ffbuild/config.log; exit 1; } >&2
    license_line="$(sed -n 's/^License: //p' "$conf_log" | tail -1)"
    case "$license_line" in
        LGPL*) log "license: $license_line" ;;
        *) echo "REFUSING: expected LGPL, got '${license_line:-unknown}'" >&2; exit 1 ;;
    esac
    log "building FFmpeg"
    make -j"$(nproc)" > "$OUT/make.log" 2>&1 || { tail -40 "$OUT/make.log" >&2; exit 1; }
    make install >> "$OUT/make.log" 2>&1
    cd "$src"
fi
# The MSVC toolchain installs the import libraries beside the DLLs.
for f in "$prefix"/bin/*.lib; do [ -e "$f" ] && mv "$f" "$prefix/lib/"; done

# ---- PyAV ---------------------------------------------------------------
log "building PyAV $AV_VERSION"
"$PY" -m pip install --quiet --upgrade pip delvewheel
rm -rf "$OUT/dist" "$OUT/fixed"
INCLUDE="$(cygpath -w "$prefix/include");${INCLUDE:-}" \
LIB="$(cygpath -w "$prefix/lib");${LIB:-}" \
    "$PY" -m pip wheel --no-binary av --no-deps "av==$AV_VERSION" \
    -w "$(cygpath -w "$OUT/dist")" > "$OUT/pyav.log" 2>&1 \
    || { tail -40 "$OUT/pyav.log" >&2; exit 1; }
raw="$(ls "$OUT"/dist/av-*.whl)"
"$PY" -m delvewheel repair --add-path "$(cygpath -w "$prefix/bin")" \
    -w "$(cygpath -w "$OUT/fixed")" "$(cygpath -w "$raw")" > "$OUT/delvewheel.log" 2>&1 \
    || { tail -40 "$OUT/delvewheel.log" >&2; exit 1; }
wheel="$(ls "$OUT"/fixed/av-*.whl)"
if unzip -l "$wheel" | grep -Ei 'x264|x265|vpx|dav1d|svtav1|libwebp|opus' >&2; then
    echo "REFUSING: the wheel carries a forbidden library (above)" >&2
    exit 1
fi

# ---- Does it work? -------------------------------------------------------
log "checking the wheel"
"$PY" -m venv "$OUT/check"
chk="$OUT/check/Scripts/python.exe"
"$chk" -m pip install --quiet "$(cygpath -w "$wheel")"
"$chk" "$(cygpath -w "$here/../tools/check_av_wheel.py")"
cp "$wheel" "$OUT/wheels/"
log "WHEEL OK -> $OUT/wheels/$(basename "$wheel")"
