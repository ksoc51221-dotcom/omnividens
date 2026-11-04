# Omnividens Cloud (decided 3 Nov 2026)

Why: school Wi-Fi (Securly) intercepts `api.telegram.org`, and the owner won't use a VPN, a hotspot or a Mac left on 24/7. So the bot moves to the cloud.

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
- The routine (Claude) reads the bundle, fact-checks per `reelay/factcheck/prompt.py`, optionally trials a found GitHub tool inside the VM, and writes `<workdir>/verdict.json` in the schema `reelay/factcheck/parse.py` validates.
- `python -m reelay.cloud report <workdir>`: validates `verdict.json` with the existing parser, formats ONE report with the existing formatting, sends it via `/api/send` (with `adopt_line` when a repo was found), updates the profile.
