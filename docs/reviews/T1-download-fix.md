# T1 download fix: review by T6 (agent/t1-download-fix @ 0dd86d1, read-only)
Suite on the branch: 315 pass. Read `download.py`, `errors.py`, `pipeline.py`, `frames.py`, the new tests.

## BLOCKER
- **The 10-minute cap now fails open.** `duration<=?600` lets any link with no duration through, so the only cap left is `pipeline.py`'s `probe_duration(...) > MAX_DURATION_S`. But `probe_duration` returns `0.0` when ffprobe is missing, prints `N/A`, or fails, and `float("nan")` parses fine and `nan > 600` is False. Before this change a missing duration was rejected; now such a file goes straight to `extract_frames` (scene-detect scans the whole file, unbounded) and the bundle claims `duration_s=0`. Fix: for downloaded files, treat unknown/NaN/≤0 duration as too long (raise `TooLongError`, or `DownloadError` "can't read its length"), and treat a missing ffprobe as an error rather than 0. Also pass `-t 600` to the frame/whisper ffmpeg calls as a backstop.

## FIX
- **Live streams and endless HLS get through.** Live videos have no `duration`, so `<=?` now passes them. The 180 s timeout bounds time, but `--max-filesize` is not reliable for fragment/unknown-size streams, so 180 s of fast download can exceed 200 MB. Use `--match-filter "duration<=?600 & !is_live"`, and after download check `video.stat().st_size <= 200 MB` (raise `TooLongError`) so the size cap holds for every path, including the merged `bv+ba` output.
- **Timeout kills only yt-dlp.** `subprocess.run(timeout=)` leaves ffmpeg children it started running. Run in its own process group and `killpg` on timeout (same helper as `adapters/base.py::subprocess_runner`). Also delete the partial `out_dir` on any failure.
- **Tests miss the new risk.** Add: (a) a download whose `probe_duration` is 0.0 / nan / > 600 → rejected; (b) ffprobe missing → rejected; (c) `!is_live` in the argv; (d) oversize file after download → `TooLongError`; (e) `subprocess.TimeoutExpired` → `NetworkError` naming the host. `test_missing_duration_is_not_rejected` only proves the happy path with a real clip (and silently skips without ffmpeg).

## NIT
- Private hosts: unchanged and fine for the first URL, but `is_private_host` returns False for names that don't resolve, and yt-dlp's generic extractor follows redirects, so a public link can redirect to `http://169.254.169.254/` or an internal host. Pre-existing; now more reachable because plain `.mp4` links are the main path. Consider `--no-follow-redirects`-style handling or checking the final URL in the info json, and note DNS rebinding as accepted.
- Messages: `user_message` only contains the hostname and never suggests disabling certificate checks (tested). Good. Host comes from the user's URL; limit it to `[A-Za-z0-9.-]` (≤ 60 chars) in case T8 uses a parse mode. The exception string (`host unreachable: <last stderr line>`) can contain the full URL with a token; fine while only `user_message` is shown, but don't log it at INFO.
- `_NETWORK_HINTS` match the whole stderr, which includes the user's URL text; a path containing `ssl:` or `timed out` would be shown as a network problem. Cosmetic: match only lines starting with `ERROR`.
- No HTTP 403/429 hint ("blocked by the site") yet; generic message is fine.

**Verdict:** not approved until the BLOCKER and the two cap FIXes (live, size) land, with tests.

## Re-review by T6 (agent/t1-download-fix @ 03fee99, read-only; 327 tests pass)
**Original BLOCKER + FIXes**
- **BLOCKER length cap: fixed.** `probe_duration` returns 0.0 for non-finite/≤0, pipeline raises `FrameError` on `not duration > 0` (covers missing ffprobe, N/A, NaN, local files too). `-t 600` added to scene detect and whisper audio.
- **Live streams: fixed** (`& !is_live`, flag accepted by the real yt-dlp). **Size: fixed** (post-download `st_size` check, partial folder removed on any exception).
- **Process group + timeout: fixed** (`_run_group`, `killpg`); ffmpeg/ffprobe have `run_tool` timeouts (R-14). **R-7 fixed** (`--ignore-config`, `--`; I ran the argv against yt-dlp 2026.10.26: parsed, tried to connect). **R-10** hourless VTT: regex looks right.
- NITs: host sanitising and ERROR-line-only network hints done.

**New BLOCKER (regression from the sanitising change)**
- **`is_private_host` now uses `safe_host()`, which strips `:` and other characters, so IPv6 literals are mangled and private IPv6 hosts pass.** Verified on the branch (on `main` all four are blocked):
  - `http://[::ffff:127.0.0.1]/x` → host `ffff127.0.0.1` → **False** (loopback)
  - `http://[fd00::1]/x` → `fd001` → **False** (unique-local)
  - `http://[fe80::1]/x` → `fe801` → **False** (link-local)
  - `[::1]` is still blocked, only by accident (`1` resolves as `0.0.0.1`).
  Fix: `is_private_host` must use the raw `urlparse(url).hostname`; keep `safe_host` for messages only. Add parametrized tests for those three plus IPv4-mapped `[::ffff:10.0.0.1]`. The existing test list only has `[::1]`, which is why it slipped through.

**FIX (small)**
- `_run_group`: `os.killpg` raises `ProcessLookupError` if yt-dlp exits in the same instant; wrap in `try/except OSError` (same as `adapters/base.py`).

**NIT**
- ERROR lines can still contain the user's URL text, so a path with `ssl:` is shown as a network problem. Cosmetic.

**Verdict: not approved yet.** One fix: use the raw hostname in `is_private_host`, with the IPv6 tests. Everything else is good, so no further re-review is needed after that: sign-off by diff is enough.

## Sign-off by T6 (agent/t1-download-fix @ 362023d, diff vs 03fee99; 334 tests pass)
- `is_private_host` uses the raw `urlparse(url).hostname` again; `safe_host` is for messages only. I ran all four IPv6 forms plus `[::1]` on the branch: all True.
- Tests cover `[::ffff:127.0.0.1]`, `[fd00::1]`, `[fe80::1]`, `[::ffff:10.0.0.1]`, `[::1]`, `[::]`, `0.0.0.0`, and the `download()` refusal path. Extra: IPv4-mapped unwrapping, multicast/unspecified blocked.
- `killpg` wrapped in `try/except OSError`.

**APPROVED** by T6.
