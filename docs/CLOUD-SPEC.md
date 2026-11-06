# Omnividens Cloud (decided 3 Nov 2026)

Why: a network that intercepts TLS (re-signs `api.telegram.org`) breaks a bot running on a laptop, and the owner won't use a VPN, a hotspot or a Mac left on 24/7. So the bot moves to the cloud.

## Flow

1. Owner shares a video or link to the Telegram bot.
2. **Relay** (Deno Deploy, free, sign in with GitHub, about 200 lines, always on) receives Telegram's webhook.
   - Checks `X-Telegram-Bot-Api-Secret-Token` and that the sender is the owner's Telegram user ID. Anything else is dropped silently, so strangers can't spend usage or inject prompts.
   - Replies at once: "Got it. Takes a few minutes."
   - Answers Adopt/Skip buttons itself, without a routine run.
   - Fires the routine's `/fire` endpoint (bearer token held only by the relay).
   - Holds the bot token. The routine sends replies and fetches Telegram files through the relay with its own secret, so the bot token never enters the cloud VM.
   - Stores the profile and memory in Deno KV.
3. **Routine** (Claude Code scheduled task, API trigger, fresh Ubuntu VM per run)
   - A setup script installs ffmpeg and yt-dlp.
   - Runs the existing pipeline: video → frames + transcript → fact-check (the subscription, no API key) → trial in this disposable VM → one report back through the relay.
   - Typed instructions mean "work on the GitHub repo and open a draft PR".

## Keeps

`reelay/video`, `reelay/factcheck`, `reelay/sandbox` (scan, safety, trial), report formatting, every hard rule (video content is data, no TLS weakening, Adopt never installs).

## Drops

PTB polling layer, launchd service, `doctor` network check, local Ollama trials, T21 local Bot API server.

## Limits (docs, research preview)

- Schedule at most hourly, hence the relay for instant replies.
- 30 API fires per task per hour, 100 per account per hour.
- Runs use Pro usage.
- Default network allowlist blocks Telegram, TikTok and YouTube. Set the environment to Custom or Full.
- Nothing persists between runs except pushed git branches.

## Risks

1. YouTube and TikTok may block datacenter IPs. **This is tested first.** Fallback: send the video file instead of a link.
2. VM start time makes each report take minutes.
3. Preview limits may change.
4. Pro usage drain.

## Feasibility result (3 Nov 2026, routine run, 58 s, network Full)

- Sandbox up in about 2 s. yt-dlp 2026.10.26 and ffmpeg 6.1.1 are preinstalled or pip-installable.
- `api.telegram.org`: reachable (302).
- **TikTok: WORKS.** Profile item, 7 MB, 43 s; ffprobe and frame extraction OK.
- **YouTube: FAILED.** "n challenge solving failed", then 403 from googlevideo. yt-dlp has no EJS solver or deno installed, so this may be fixable: retry with deno + `yt-dlp[default]` in the setup script. If it is still blocked, fall back to YouTube subtitles/metadata only, or the video file.

## Owner does

Create the GitHub repo, sign in to Deno Deploy with GitHub, and paste the secrets (bot token, `/fire` token, relay secret) into the relay and routine settings. Claude never handles tokens.

## Order

1. Feasibility routine: can the VM download a TikTok and a YouTube Short, and reach `api.telegram.org`?
2. Relay (Deno Deploy).
3. Routine prompt + setup script + `python -m reelay.cloud` entry point.
4. Live test.
5. README, demo.

## Contract (T23a relay ↔ T23b routine)

**Relay env vars** (set by the owner in the Deno Deploy dashboard):
- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`: if unset, the relay answers any `/start` with "Your Telegram ID is N".
- `WEBHOOK_SECRET`
- `RELAY_SECRET`
- `ROUTINE_FIRE_URL`
- `ROUTINE_TOKEN`
- `MAX_RUNS_PER_HOUR` (default 20)

**Relay routes:**

- `GET /`: idempotently calls Telegram `setWebhook(url=<own origin>/telegram, secret_token=WEBHOOK_SECRET, allowed_updates=["message","callback_query"])`. Shows an HTML status page ("Connected ✓" / which env vars are missing, never their values).
- `POST /telegram`: Telegram webhook.
  - Rejects a wrong `X-Telegram-Bot-Api-Secret-Token` with 401.
  - Dedupes by `update_id` (KV, 24 h).
  - Non-owner: ignored with 200 (except the ID hint when the owner is unset).
  - `callback_query` `adopt:<id>`: replies with the stored clone line (KV `adopt/<id>`). `skip:<id>`: replies "Skipped." Both answer the callback.
  - Owner message (text, video, document, animation):
    - `/help` or `/start` with no file: reply with the list of commands. No routine run, no rate slot used.
    - Nothing a run could work on (no link, no `/report owner/repo`, and no video file of 20 MB or less): reply with what to send instead. No routine run, no rate slot used.
    - Over `MAX_RUNS_PER_HOUR`: reply "Busy, try again later".
    - Otherwise reply "Got it. Takes a few minutes.", then `POST ROUTINE_FIRE_URL` with `Authorization: Bearer ROUTINE_TOKEN`, `anthropic-beta: experimental-cc-routine-2026-04-01`, `content-type: application/json`, body `{"text": "<JOB JSON>"}`.
    - On a fire failure, tell the owner in one plain line.
- **JOB JSON:** `{"v":1,"update_id":n,"chat_id":n,"message_id":n,"text":"...","file":{"file_id":"...","file_size":n,"mime_type":"..."}|null,"relay_url":"https://..."}`. Text is capped at 4000 chars.
- Routine API, every call needing `Authorization: Bearer RELAY_SECRET` (constant-time compare):
  - `POST /api/send` `{"chat_id":n,"text":"...","adopt_line":"git clone -- https://github.com/o/r"|null}`
    - Sends `text`: plain, no parse_mode, split at 4096.
    - With `adopt_line` (must match `^git clone -- https://github\.com/[\w.-]+/[\w.-]+$`), stores it under a random id and attaches Adopt/Skip buttons.
  - `GET /api/file/<file_id>`: `getFile`, then streams the bytes. Refuses files over 20 MB.
  - `GET /api/profile` returns the JSON object (or `{}`). `PUT /api/profile` stores the JSON (64 KB max).

**Routine side.** The VM never sees the bot token. The relay secret reaches it as an environment "network secret": the proxy adds `Authorization: Bearer RELAY_SECRET` for the relay host.
- `python -m reelay.cloud prepare '<JOB JSON>' <workdir>`:
  - Downloads (link via yt-dlp, or the file via `/api/file`).
  - Frames + transcript via `reelay.video`.
  - Fetches the profile.
  - Writes `<workdir>/job.json` and `bundle.json` (frames paths, transcript, caption, links found).
  - Exits 0. On failure: sends a plain error via `/api/send`, exits 2.
- The routine (Claude) reads the bundle and first looks for GitHub candidates. If it finds any, it skips claim checking (`claims: []`, no web search) and writes a short report per candidate; with no candidates it fact-checks per `reelay/factcheck/prompt.py`. It reads (never runs) each clone, and writes `<workdir>/verdict.json` in the schema `reelay/factcheck/parse.py` validates.
- `python -m reelay.cloud report <workdir>`: validates `verdict.json` with the existing parser, formats ONE report with the existing formatting, sends it via `/api/send`, updates the profile. `adopt_line` (the Adopt/Skip buttons) is set only for a skill or mod with confirmed identity, file evidence, safety YES and no code in the repo. A skill with code the scan can't judge (UNSURE) gets NO buttons (`adopt_line` null): the report text warns that the code was not read or run, names the files to read first, and shows the `git clone -- https://github.com/owner/repo` line for the owner to run by hand. Per-candidate report: name and exact owner/repo, safety line, two-sentence summary, usage (token cost measured by the report step from `SKILL.md` sizes in its own clone, about bytes / 4; falls back to the model's number labelled as an estimate), and impact ("Could help <project>: ..." or doesn't fit). Old `problem`/`pros`/`cons`/`examples` fields are accepted and ignored. No empty fact-check section when `claims` is empty.

## Mac fallback (T25, 5 Nov 2026)

YouTube answers the cloud VM with "confirm you're not a bot". The owner's Mac can fetch the same link, so a blocked link goes to a queue the Mac polls. The Mac only fetches: no model, no routine (a model reading hostile text stays on the disposable VM). The video never leaves the Mac; frames and transcript do.

**Flow:** `prepare` gets `BlockedError` for a link job → `POST /api/defer` (RELAY_SECRET) → relay queues it and tells the user (Mac seen in the last 3 minutes: "your Mac is fetching this one"; otherwise "your Mac isn't online (last seen …), I'll keep this for 24 hours") → `prepare` exits 3 → Mac `GET /api/mac/next` claims it → downloads, samples frames (scene cuts, thinned with `plan_times` to 20, 512 px, ≤50 KB each) → `POST /api/mac/bundle` → relay stores it and fires the routine with the original JOB plus `bundle_id` (same hourly cap) → `prepare` sees `bundle_id`, fetches `GET /api/bundle/<id>` (RELAY_SECRET), validates and writes `bundle.json` and the frames itself → unchanged fact-check, Adopt gate and reports. A job with `bundle_id` is never deferred again.

**Env:** `MAC_SECRET`, used by the `/api/mac/*` routes only. `RELAY_SECRET` opens none of them and `MAC_SECRET` opens nothing else. Unset means the Mac routes are closed and `/api/defer` answers `{"queued": false}` (the user gets the plain "blocked from the cloud" reply).

**Routes**
- `POST /api/defer` (RELAY_SECRET) `{"job": JOB}`. Owner chat only, link jobs only (no `file`, no `bundle_id`), one queue entry per `update_id` (a repeat answers `duplicate`). Returns `{"queued": bool, "online": bool}`.
- `GET /api/mac/next` (MAC_SECRET): records the heartbeat, sweeps, claims the oldest queued job with a 15-minute lease. 204 if none. Returns `{update_id, lease, attempt, job}`. Two attempts; a job whose second lease runs out is dropped and the user told.
- `POST /api/mac/bundle` (MAC_SECRET) `{update_id, lease, meta:{duration_s, transcript, caption}, frames:[{time_s, jpeg_b64}]}`. At most 20 frames, each a JPEG (magic bytes checked) of at most 50,000 bytes, stored as separate KV values (KV values max 64 KiB); transcript trimmed to 24,000 chars and caption to 3,000 so metadata fits one value; body at most 1.5 MB. Expires in 24 h. A stale lease gets 409. If the hourly cap is full the bundle waits and fires on the next sweep.
- `POST /api/mac/fail` (MAC_SECRET) `{update_id, lease, reason: blocked|rate_limited|bad_link|network|too_long|other, host?}`: the relay writes the user's message from a fixed table (no Mac text is relayed) and drops the job.
- `GET /api/bundle/<id>` (RELAY_SECRET): the routine reads the upload.

**Expiry:** a job lives 24 h from queueing, then the user is told, naming the link, and asked to resend. The KV row lives 48 h so a sweep can still find it. Sweeps run on every Telegram webhook, every worker poll, and a 10-minute `Deno.cron` if the platform has it (unstable API, enabled in `relay/deno.json`; not verified live on this Deploy setup). Without cron the message can be late when nothing touches the relay.

**Errors (Python):** `BlockedError` (bot check), `RateLimitedError` (HTTP 429 on the video), `BadLinkError` (private, removed, unsupported), `NetworkError` (host unreachable). Each has `user_message` (local mode) and `cloud_message` where the advice differs. yt-dlp runs once with all retries off; subtitles ask for `en,en-US,en-GB,en-orig` only and whichever `.vtt` arrives is used. No captions means the report says so.
