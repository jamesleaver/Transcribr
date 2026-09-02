#!/bin/bash
# Transcribr - one-line installer for macOS
#
#   curl -fsSL https://raw.githubusercontent.com/jamesleaver/Transcribr/main/macos/bootstrap.sh | bash
#
# Why this exists: anything downloaded by a *browser* is tagged
# com.apple.quarantine, and macOS then refuses to run it until you go
# digging through System Settings. Files fetched with curl carry no such
# tag, so this path has no Gatekeeper prompt at all - the same reason
# Homebrew installs itself this way.
#
# All this does is fetch the current release, check it against the
# checksum GitHub publishes for the file, and hand it to the real
# installer (macos/install.command), which does the actual work.
#
# Optionally pin a version:
#   curl -fsSL .../bootstrap.sh | bash -s -- v0.9.13

set -uo pipefail

REPO="jamesleaver/Transcribr"
WANTED="${1:-}"

if [ -t 1 ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; RED=""; RESET=""
fi
say()  { echo "${GREEN}==>${RESET} $*"; }
fail() { echo "${RED}Error:${RESET} $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "This installer is for macOS. On Windows use windows\\install.bat."

# ---- find the release ------------------------------------------------------

if [ -n "$WANTED" ]; then
    API="https://api.github.com/repos/$REPO/releases/tags/$WANTED"
    say "Looking up Transcribr $WANTED..."
else
    API="https://api.github.com/repos/$REPO/releases/latest"
    say "Looking up the latest Transcribr release..."
fi

META="$(curl -fsSL "$API")" \
    || fail "Couldn't reach GitHub. Check your internet connection and try again."

# Parsed with grep/sed rather than python3 or jq: neither is guaranteed
# to be present on a Mac that hasn't installed the developer tools yet.
TAG="$(printf '%s' "$META" \
    | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
ASSET_URL="$(printf '%s' "$META" \
    | grep -o '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*\.zip"' \
    | head -1 | sed 's/.*"\(https[^"]*\)"$/\1/')"
DIGEST="$(printf '%s' "$META" \
    | grep -o '"digest"[[:space:]]*:[[:space:]]*"sha256:[0-9a-f]*"' \
    | head -1 | sed 's/.*sha256:\([0-9a-f]*\)"$/\1/')"

[ -n "$ASSET_URL" ] || fail "That release has no installer download. See https://github.com/$REPO/releases"

# ---- fetch it --------------------------------------------------------------

TMP="$(mktemp -d -t transcribr-install)" || fail "Couldn't create a temporary folder."
trap 'rm -rf "$TMP"' EXIT

ZIP="$TMP/installer.zip"
say "Downloading Transcribr ${TAG:-release}..."
curl -fsSL --retry 3 -o "$ZIP" "$ASSET_URL" \
    || fail "Download failed. Try again, or download it by hand from https://github.com/$REPO/releases"

if [ -n "$DIGEST" ]; then
    GOT="$(shasum -a 256 "$ZIP" | awk '{print $1}')"
    if [ "$GOT" != "$DIGEST" ]; then
        fail "The download didn't match GitHub's checksum. Nothing has been installed."
    fi
    say "Checksum verified."
fi

say "Unpacking..."
/usr/bin/unzip -q -o "$ZIP" -d "$TMP/src" || fail "Couldn't unpack the download."

INSTALLER="$(/usr/bin/find "$TMP/src" -name install.command -type f -print -quit)"
[ -n "$INSTALLER" ] || fail "The download didn't contain macos/install.command."
chmod +x "$INSTALLER"

# ---- hand over to the real installer ---------------------------------------

say "Starting the installer..."
echo

# This script is usually itself running from a pipe (curl | bash), so the
# installer would inherit a spent stdin and blow straight through its
# y/n prompts. Give it the terminal back.
if [ -r /dev/tty ]; then
    "$INSTALLER" < /dev/tty
else
    "$INSTALLER"
fi
STATUS=$?

echo
if [ $STATUS -eq 0 ]; then
    say "${BOLD}Done.${RESET} Launch Transcribr from Spotlight, Launchpad or Applications."
else
    fail "The installer exited with status $STATUS."
fi
