#!/usr/bin/env bash
# Build an LGPL FFmpeg carrying no external codec libraries, for PyAV.
#
# Transcribr decodes and remuxes audio and encodes only AAC and PCM, both
# of which are FFmpeg's own native encoders. Every external codec library
# the stock PyAV wheel vendors is therefore dead weight - and two of
# them, x264 and x265, are GPL, which is what stops the source ever being
# closed. None are built here.
#
# The one exception is LAME. The app never encodes mp3, but the suite's
# mp3 round-trip test must build a broken mp3 to prove the remux repairs
# it, and FFmpeg has no native mp3 encoder. LAME is LGPL-2.1, so it costs
# nothing licence-wise. It is linked statically into libavcodec, so the
# wheel gains no extra library and cannot pick up a host copy.
#
# Every autodetectable host library is disabled EXPLICITLY. FFmpeg's
# configure otherwise picks up whatever the build machine happens to
# have: Homebrew's sdl2 and X11 both turned up this way, and the X11 one
# silently pinned the wheel to the macOS version of the build machine.
set -euo pipefail

FFMPEG_VERSION="${FFMPEG_VERSION:-8.1.2}"
LAME_VERSION="${LAME_VERSION:-3.100}"
PREFIX="${PREFIX:?set PREFIX to the install directory}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
# Extra configure flags, e.g. --disable-x86asm for an Intel build on a
# machine without nasm (audio decoding barely uses the hand-written
# assembly, so the cost is negligible).
EXTRA_CONFIGURE="${EXTRA_CONFIGURE:-}"

# Anything matching this must never end up linked into the wheel.
FORBIDDEN='libx264|libx265|libvpx|libdav1d|libSvtAv1|libwebp|libopus|libopencore|libX11|libxcb|libsharpyuv|libmp3lame\.[0-9]'

work="$(dirname "$PREFIX")/src"
mkdir -p "$work"
cd "$work"

tarball="ffmpeg-$FFMPEG_VERSION.tar.xz"
# A truncated download unpacks far enough to look plausible and then
# fails deep in the build, so verify the archive before trusting it.
if [ -f "$tarball" ] && ! tar -tf "$tarball" >/dev/null 2>&1; then
    echo "discarding a corrupt $tarball" >&2
    rm -f "$tarball"
fi
if [ ! -f "$tarball" ]; then
    curl -fL --retry 5 --retry-all-errors -o "$tarball.part" \
        "https://ffmpeg.org/releases/$tarball"
    tar -tf "$tarball.part" >/dev/null || { echo "download is corrupt" >&2; exit 1; }
    mv "$tarball.part" "$tarball"
fi
if [ ! -d "ffmpeg-$FFMPEG_VERSION" ]; then
    tar -xf "$tarball"
fi
# ---- LAME, static, into the same prefix -----------------------------
lame_tar="lame-$LAME_VERSION.tar.gz"
if [ -f "$lame_tar" ] && ! tar -tzf "$lame_tar" >/dev/null 2>&1; then
    rm -f "$lame_tar"
fi
if [ ! -f "$lame_tar" ]; then
    curl -fL --retry 5 --retry-all-errors -o "$lame_tar.part" \
        "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/$lame_tar"
    tar -tzf "$lame_tar.part" >/dev/null || { echo "LAME download is corrupt" >&2; exit 1; }
    mv "$lame_tar.part" "$lame_tar"
fi
if [ ! -f "$PREFIX/lib/libmp3lame.a" ]; then
    rm -rf "lame-$LAME_VERSION"
    tar -xzf "$lame_tar"
    (
        cd "lame-$LAME_VERSION"
        # 3.100's export list names a symbol it no longer defines; only
        # the shared build reads it, but drop it so neither can trip.
        sed -i.bak '/lame_init_old/d' include/libmp3lame.sym
        ./configure --prefix="$PREFIX" --disable-shared --enable-static \
            --with-pic --disable-frontend --disable-decoder \
            --disable-dependency-tracking > "$work/lame.log" 2>&1
        make -j"$JOBS" >> "$work/lame.log" 2>&1
        make install >> "$work/lame.log" 2>&1
    ) || { tail -30 "$work/lame.log" >&2; exit 1; }
fi

cd "ffmpeg-$FFMPEG_VERSION"

# configure reports the licence and the external libraries on stdout and
# nowhere else - config.log does not carry them - so keep the output.
conf_log="$work/configure-output.log"
./configure \
    --prefix="$PREFIX" \
    --disable-static --enable-shared \
    --disable-programs --disable-doc \
    --enable-version3 \
    --enable-zlib \
    --enable-libmp3lame \
    --extra-cflags="-I$PREFIX/include" \
    --extra-ldflags="-L$PREFIX/lib" \
    --enable-videotoolbox --enable-audiotoolbox \
    --disable-libxml2 --disable-lzma --disable-libtheora --disable-libfreetype \
    --disable-libfontconfig --disable-libbluray --disable-libopenjpeg \
    --disable-alsa --disable-gnutls --disable-libjack --disable-indev=jack \
    --disable-mediafoundation \
    --disable-sdl2 --disable-xlib --disable-libxcb \
    --disable-encoder='avui,dca,mlp,opus,s302m,sonic,sonic_ls,truehd' \
    --disable-decoder=sonic \
    ${CC:+"--cc=$CC"} \
    ${EXTRA_CONFIGURE:-} \
    > "$conf_log" 2>&1 || { tail -30 "$conf_log" >&2; exit 1; }

# Refuse to go further if this is not an LGPL build. Cheaper to fail here
# than to find out from a shipped wheel.
license_line="$(sed -n 's/^License: //p' "$conf_log" | tail -1)"
case "$license_line" in
    LGPL*) echo "license: $license_line" ;;
    *) echo "REFUSING: expected LGPL, got '${license_line:-unknown}'" >&2
       exit 1 ;;
esac

make -j"$JOBS" > "$work/make.log" 2>&1 || { tail -30 "$work/make.log" >&2; exit 1; }
make install >> "$work/make.log" 2>&1

# The decisive check: what did we actually link? This catches a host
# library that slipped past configure as well as one we forgot to
# disable.
linked="$(otool -L "$PREFIX"/lib/libav*.dylib "$PREFIX"/lib/libsw*.dylib \
          | grep -oE "$FORBIDDEN" | sort -u || true)"
if [ -n "$linked" ]; then
    echo "REFUSING: forbidden libraries linked into the build:" >&2
    echo "$linked" >&2
    exit 1
fi

echo "BUILD OK -> $PREFIX"
du -ch "$PREFIX"/lib/*.*.dylib | tail -1
