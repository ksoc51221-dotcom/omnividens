#!/usr/bin/env bash
# Idempotent setup for the Reelay routine VM (Ubuntu). Safe to run twice.
set -u
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  $SUDO apt-get update -y >/dev/null 2>&1 || true
  $SUDO apt-get install -y ffmpeg || echo "setup: ffmpeg install failed"
fi

# yt-dlp[default] brings the EJS challenge solver pieces; the deno wheel from PyPI is the JS runtime
# YouTube needs.
python3 -m pip install -q -U "yt-dlp[default]" deno || echo "setup: yt-dlp/deno install failed"
# faster-whisper: free CPU speech-to-text for videos without subtitles (model downloads from huggingface.co)
python3 -m pip install -q -U faster-whisper || echo "setup: faster-whisper install failed (transcripts: subtitles only)"
python3 -m pip install -q -e . || echo "setup: pip install -e . failed (run from the repo root)"

# the deno wheel puts its binary in the pip scripts dir; make sure that is on PATH for this shell
export PATH="$HOME/.local/bin:$PATH"
echo "ffmpeg:  $(ffmpeg -version 2>/dev/null | head -1)"
echo "yt-dlp:  $(yt-dlp --version 2>/dev/null)"
echo "deno:    $(deno --version 2>/dev/null | head -1)"
echo "python:  $(python3 --version 2>&1)"
