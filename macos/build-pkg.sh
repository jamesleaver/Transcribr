#!/bin/bash
# Transcribr - build a signed, notarised macOS installer package.
#
#   ./macos/build-pkg.sh                 # sign + notarise (needs certs)
#   ./macos/build-pkg.sh --adhoc         # no certs: assemble and
#                                        # ad-hoc sign, for testing
#   ./macos/build-pkg.sh --no-notarize   # sign properly, skip Apple
#   ./macos/build-pkg.sh --arch x86_64   # Intel build (default: arm64)
#
# Produces dist/Transcribr-<version>-<arch>.pkg.
#
# The package is self-contained: a relocatable CPython and the default
# engine travel inside the app bundle, so installing needs no Homebrew,
# no python.org download and no network. See RELEASING.md for the
# one-time Apple Developer setup this expects.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY_SERIES="3.12"
ARCH="arm64"
ADHOC=0
NOTARIZE=1

while [ $# -gt 0 ]; do
    case "$1" in
        --adhoc)       ADHOC=1; NOTARIZE=0 ;;
        --no-notarize) NOTARIZE=0 ;;
        --arch)        ARCH="${2:-arm64}"; shift ;;
        -h|--help)     sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -t 1 ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
    RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
say()  { echo "${GREEN}==>${RESET} $*"; }
warn() { echo "${YELLOW}warning:${RESET} $*"; }
fail() { echo "${RED}Error:${RESET} $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "This script builds a macOS package; run it on a Mac."
command -v clang >/dev/null || fail "clang is missing. Run: xcode-select --install"
command -v codesign >/dev/null || fail "codesign is missing. Run: xcode-select --install"

case "$ARCH" in
    arm64)  PBS_TRIPLE="aarch64-apple-darwin" ;;
    x86_64) PBS_TRIPLE="x86_64-apple-darwin" ;;
    *) fail "Unknown arch '$ARCH' (use arm64 or x86_64)" ;;
esac

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' transcribr.py | head -1)"
[ -n "$VERSION" ] || fail "Couldn't read __version__ from transcribr.py"
[ -f webdist/index.html ] || fail "webdist/index.html is missing - run 'npm ci && npm run build' in web/ first."

say "Building ${BOLD}Transcribr $VERSION${RESET} for $ARCH"

BUILD="$REPO_ROOT/build/macos-$ARCH"
DIST="$REPO_ROOT/dist"
APP="$BUILD/root/Applications/Transcribr.app"
RES="$APP/Contents/Resources"
rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$RES" "$DIST"

# This repository may live in a synced folder. Dropbox happily writes
# "conflicted copy" files into a 584 MB build tree while it is being
# signed - including inside the bundle and its _CodeSignature directory,
# which invalidates the seal. Mark the build outputs as ignored; the
# attribute is harmless anywhere else.
for d in "$REPO_ROOT/build" "$DIST"; do
    xattr -w com.dropbox.ignored 1 "$d" 2>/dev/null || true
done

# ---- identities -------------------------------------------------------------

TEAM_ID="${TRANSCRIBR_TEAM_ID:-}"
NOTARY_PROFILE="${TRANSCRIBR_NOTARY_PROFILE:-transcribr-notary}"
SIGN_APP=""
SIGN_PKG=""

if [ "$ADHOC" -eq 1 ]; then
    SIGN_APP="-"
    warn "Ad-hoc signing. The result will NOT pass Gatekeeper on another Mac."
else
    find_identity() {
        security find-identity -v -p codesigning 2>/dev/null \
            | grep "$1" \
            | { [ -n "$TEAM_ID" ] && grep "($TEAM_ID)" || cat; } \
            | head -1 | sed 's/.*"\(.*\)".*/\1/'
    }
    SIGN_APP="$(find_identity 'Developer ID Application')"
    SIGN_PKG="$(security find-identity -v 2>/dev/null \
        | grep 'Developer ID Installer' \
        | { [ -n "$TEAM_ID" ] && grep "($TEAM_ID)" || cat; } \
        | head -1 | sed 's/.*"\(.*\)".*/\1/')"
    [ -n "$SIGN_APP" ] || fail "No 'Developer ID Application' certificate found. See RELEASING.md, or use --adhoc to test the build."
    [ -n "$SIGN_PKG" ] || fail "No 'Developer ID Installer' certificate found. Both certificates are needed - see RELEASING.md."
    say "Signing as: $SIGN_APP"
fi

# ---- bundled interpreter ----------------------------------------------------

say "Resolving a standalone CPython $PY_SERIES build ($PBS_TRIPLE)..."
PBS_API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
META="$(curl -fsSL "$PBS_API")" || fail "Couldn't reach GitHub for the Python runtime."
# The download URL percent-encodes the version's "+" as %2B, while the
# asset's name field keeps it literal - so match loosely between the
# version and the platform triple rather than assuming either form.
pbs_url() {
    printf '%s' "$META" \
        | grep -o "https://[^\"]*cpython-${PY_SERIES}\.[0-9][^\"]*-${PBS_TRIPLE}-$1\.tar\.gz" \
        | head -1
}
PY_URL="$(pbs_url install_only_stripped)"
[ -n "$PY_URL" ] || PY_URL="$(pbs_url install_only)"
[ -n "$PY_URL" ] || fail "No $PBS_TRIPLE build of CPython $PY_SERIES in that release."

say "Downloading $(basename "$PY_URL")..."
curl -fsSL --retry 3 -o "$BUILD/python.tar.gz" "$PY_URL" || fail "Runtime download failed."
tar -xzf "$BUILD/python.tar.gz" -C "$RES" || fail "Could not unpack the runtime."
PY="$RES/python/bin/python3"
[ -x "$PY" ] || fail "Runtime unpacked but $PY is missing."
"$PY" --version || fail "The bundled interpreter does not run."

# ---- dependencies -----------------------------------------------------------
#
# openai-whisper and mlx-whisper are deliberately absent: both require
# PyTorch, which would take the download from roughly 330 MB to 1.4 GB.
# They install in one click from the app's Models tab.

say "Installing application libraries and the faster-whisper engine..."
"$PY" -m pip install --upgrade pip --quiet || fail "Could not bootstrap pip."
"$PY" -m pip install --quiet \
    faster-whisper sherpa-onnx python-docx reportlab pywebview bottle \
    || fail "Dependency install failed."
"$PY" -c "import faster_whisper, bottle, webview, docx, reportlab; print('imports ok')" \
    || fail "The bundled runtime cannot import its own dependencies."

# ---- application files ------------------------------------------------------

say "Assembling the app bundle..."
cp transcribr.py "$RES/"
cp -R webdist "$RES/"
cp macos/app_template/bootstrap.py "$RES/"
[ -f macos/app_template/icon.icns ] && cp macos/app_template/icon.icns "$RES/"
sed "s/__VERSION__/$VERSION/g" macos/app_template/Info.plist > "$APP/Contents/Info.plist"
# The template names the shell script the *script* installer copies in.
# This build compiles a binary called Transcribr instead, and if the two
# disagree macOS cannot find the executable and refuses the bundle with
# "damaged or incomplete" - which is not a signing problem, so codesign
# and spctl --type install both still pass.
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable Transcribr" \
    "$APP/Contents/Info.plist" >/dev/null \
    || fail "Could not set CFBundleExecutable in Info.plist."

clang -O2 -Wall -arch "$ARCH" \
    -o "$APP/Contents/MacOS/Transcribr" macos/app_template/launcher.c \
    || fail "Could not compile the launcher."

# ---- prune ------------------------------------------------------------------
#
# Bytecode is rebuilt on demand, and CPython's own test suite is tens of
# megabytes nobody needs. Anything removed here must go before signing.

say "Pruning..."
BEFORE=$(du -sm "$APP" | cut -f1)
find "$RES/python" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null
rm -rf "$RES/python/lib/python$PY_SERIES/test" 2>/dev/null
rm -rf "$RES/python/lib/python$PY_SERIES/site-packages/PyObjCTest" 2>/dev/null
# Debug-symbol bundles that ride along with some wheels. pkgbuild
# otherwise treats each one as a separate bundle component.
find "$RES" -type d -name '*.dSYM' -prune -exec rm -rf {} + 2>/dev/null
AFTER=$(du -sm "$APP" | cut -f1)
say "App bundle: ${AFTER} MB (pruned $((BEFORE - AFTER)) MB)"

# ---- sanity checks ----------------------------------------------------------
#
# Both of these shipped broken once. Neither is caught by codesign,
# spctl --type install, or notarisation: a bundle can be perfectly
# signed and still refuse to launch.

EXE_NAME="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" \
    "$APP/Contents/Info.plist" 2>/dev/null)"
[ -n "$EXE_NAME" ] || fail "Info.plist has no CFBundleExecutable."
[ -f "$APP/Contents/MacOS/$EXE_NAME" ] || fail \
    "Info.plist declares CFBundleExecutable '$EXE_NAME', but Contents/MacOS/ holds: $(ls "$APP/Contents/MacOS/" | tr '\n' ' ')
macOS would refuse this bundle as \"damaged or incomplete\"."
say "Bundle executable: $EXE_NAME (present)"

STRAY="$(find "$APP" -name '*conflicted copy*' | wc -l | tr -d ' ')"
if [ "$STRAY" -ne 0 ]; then
    warn "Removing $STRAY file-sync conflicted copies from the bundle."
    find "$APP" -name '*conflicted copy*' -print0 | xargs -0 rm -rf
fi

# ---- sign -------------------------------------------------------------------
#
# Every Mach-O inside the bundle must be signed, innermost first, before
# the bundle itself is sealed. Notarisation rejects the whole package
# over a single unsigned .so.

say "Signing every binary in the bundle..."
SIGN_ARGS=(--force --timestamp --options runtime
           --entitlements "$REPO_ROOT/macos/entitlements.plist")
[ "$ADHOC" -eq 1 ] && SIGN_ARGS=(--force --options runtime
                                 --entitlements "$REPO_ROOT/macos/entitlements.plist")

# Collected to a file first: /bin/bash on macOS is 3.2 and does not
# cope with this pipeline nested inside a process substitution.
CANDIDATES="$BUILD/candidates.txt"
MACHO="$BUILD/macho.txt"
find "$APP" -type f \
    \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) > "$CANDIDATES"

: > "$MACHO"
while IFS= read -r f; do
    if file -b "$f" 2>/dev/null | grep -q 'Mach-O'; then
        printf '%s\n' "$f" >> "$MACHO"
    fi
done < "$CANDIDATES"
# Deepest paths first, so nested binaries are sealed before whatever
# contains them.
sort -r "$MACHO" -o "$MACHO"

SIGNED=0
FAILED=0
while IFS= read -r f; do
    if codesign "${SIGN_ARGS[@]}" --sign "$SIGN_APP" "$f" 2>/dev/null; then
        SIGNED=$((SIGNED + 1))
    else
        FAILED=$((FAILED + 1))
        echo "  could not sign: $f" >&2
    fi
done < "$MACHO"
say "Signed $SIGNED binaries${FAILED:+ ($FAILED failed)}"
[ "$FAILED" -eq 0 ] || fail "Some binaries could not be signed; notarisation would reject this."

say "Sealing the app bundle..."
codesign "${SIGN_ARGS[@]}" --sign "$SIGN_APP" "$APP" || fail "Could not sign the app bundle."
codesign --verify --deep --strict --verbose=2 "$APP" || fail "The signed bundle does not verify."

# ---- package ----------------------------------------------------------------

PKG="$DIST/Transcribr-$VERSION-$ARCH.pkg"
COMPONENT="$BUILD/component.pkg"

say "Building the installer package..."
pkgbuild --root "$BUILD/root" \
         --identifier "io.github.jamesleaver.transcribr" \
         --version "$VERSION" \
         --install-location "/" \
         "$COMPONENT" || fail "pkgbuild failed."

PRODUCT_ARGS=(--package-path "$BUILD" --version "$VERSION")
if [ "$ADHOC" -eq 0 ]; then
    PRODUCT_ARGS+=(--sign "$SIGN_PKG" --timestamp)
fi
productbuild "${PRODUCT_ARGS[@]}" --package "$COMPONENT" "$PKG" \
    || fail "productbuild failed."
say "Wrote $PKG ($(du -h "$PKG" | cut -f1))"

if [ "$ADHOC" -eq 1 ]; then
    warn "Ad-hoc build finished. It will not install cleanly on another Mac."
    exit 0
fi

# ---- notarise ---------------------------------------------------------------

if [ "$NOTARIZE" -eq 0 ]; then
    warn "Skipping notarisation (--no-notarize). Gatekeeper will still refuse this."
    exit 0
fi

say "Uploading to Apple for notarisation (typically 2-15 minutes)..."
if ! xcrun notarytool submit "$PKG" \
        --keychain-profile "$NOTARY_PROFILE" --wait; then
    echo
    fail "Notarisation failed. Ask Apple what it disliked:
  xcrun notarytool history --keychain-profile \"$NOTARY_PROFILE\"
  xcrun notarytool log <submission-id> --keychain-profile \"$NOTARY_PROFILE\""
fi

say "Stapling the ticket..."
xcrun stapler staple "$PKG" || fail "Stapling failed."

# ---- verify -----------------------------------------------------------------

say "Verifying..."
xcrun stapler validate "$PKG" || warn "stapler validate was not happy."
spctl --assess --type install -vv "$PKG" 2>&1 | sed 's/^/    /'
pkgutil --check-signature "$PKG" | head -4 | sed 's/^/    /'

echo
say "${BOLD}Done:${RESET} $PKG"
say "Test it properly by downloading it in a browser on another Mac -"
say "no warning at all is the goal."
