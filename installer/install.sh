#!/bin/sh
# Reelay installer for macOS. Needs no admin password. Run it with:
#
#   curl -fsSL https://raw.githubusercontent.com/REPLACE-ME/reelay/main/installer/install.sh | sh
#
# What it does, in order (nothing else):
#   1. installs `uv` (a Python tool manager) into ~/.local/bin if you don't have it
#   2. installs Reelay (and yt-dlp, which it needs) as a uv tool from the GitHub repo
#   3. makes sure ~/.local/bin is on your PATH for new Terminal windows
#   4. starts `reelay setup`, the friendly setup wizard
# Undo everything later with:  reelay service remove; uv tool uninstall reelay

set -eu

REELAY_REPO="${REELAY_REPO:-https://github.com/REPLACE-ME/reelay}"
REELAY_REF="${REELAY_REF:-main}"
UV_INSTALLER="https://astral.sh/uv/install.sh"

say() { printf '%s\n' "$*"; }
fail() { say ""; say "Something went wrong: $*"; say "Nothing was broken. Fix the problem above and run the same command again."; exit 1; }

[ "$(uname -s)" = "Darwin" ] || say "Note: Reelay is tested on macOS only. Carrying on anyway."
[ "$(id -u)" -ne 0 ] || fail "please run this as your normal user, not with sudo."
case "$REELAY_REPO" in *REPLACE-ME*) fail "the GitHub address in this script is still a placeholder. Set REELAY_REPO=<repo url> first." ;; esac

say "Installing Reelay. This needs no admin password."

# 1. uv
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Step 1/3: installing uv (Python tool manager)..."
  curl -LsSf "$UV_INSTALLER" | env UV_NO_MODIFY_PATH=1 sh || fail "couldn't install uv. Check your internet connection."
  command -v uv >/dev/null 2>&1 || fail "uv was installed but I can't find it. Open a new Terminal window and run the command again."
else
  say "Step 1/3: uv is already installed."
fi

# 2. Reelay itself (uv fetches a suitable Python by itself if the Mac's is too old)
say "Step 2/3: installing Reelay..."
uv tool install --force --with-executables-from yt-dlp "git+${REELAY_REPO}@${REELAY_REF}" \
  || fail "couldn't download or install Reelay from ${REELAY_REPO}."

# 3. PATH for future windows (edits your shell startup file; harmless and reversible)
say "Step 3/3: making the 'reelay' command available in new Terminal windows..."
uv tool update-shell >/dev/null 2>&1 || say "(Couldn't edit your shell settings. If 'reelay' isn't found later, add ~/.local/bin to your PATH.)"

REELAY_BIN="$(command -v reelay || true)"
[ -n "$REELAY_BIN" ] || REELAY_BIN="$HOME/.local/bin/reelay"
[ -x "$REELAY_BIN" ] || fail "Reelay installed but I can't find the 'reelay' command."

say ""
say "Reelay is installed. Starting the setup wizard (about 5 minutes)..."
# When run as `curl | sh`, stdin is the download, so read the keyboard from the terminal instead.
if (: </dev/tty) 2>/dev/null; then
  exec "$REELAY_BIN" setup </dev/tty
fi
say "Open a new Terminal window and type:  reelay setup"
