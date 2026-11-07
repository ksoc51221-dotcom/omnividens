# Omnividens

[![CI](https://github.com/ksoc51221-dotcom/omnividens/actions/workflows/ci.yml/badge.svg)](https://github.com/ksoc51221-dotcom/omnividens/actions/workflows/ci.yml)

**Share a short video to your own Telegram bot. Claude finds the skill or tool it shows, scans the repo, and tells you what it costs in tokens, where it fits your projects and how useful it is, before you adopt anything.**

Omnividens runs in the cloud: a tiny relay receives your Telegram message, and a Claude Code routine does the work on a fresh VM. Your laptop can be closed.

Built for the November 2026 hackathon. The Python package and CLI are still called `reelay` (the project's first name). What has and hasn't been run live is listed under [Limitations](#limitations).

## What it does

- 🎬 **Watches** a TikTok link or an uploaded video file: downloads it, samples frames, and gets a transcript (native subtitles, or free on-VM Whisper when there are none).
- 💬 **Two messages.** First: one sentence on what the skill does, then "This test will take approximately N minutes". Then the report: tokens per use, the projects it applies to, a rating out of 10, and Skip / Adopt buttons.
- 🧪 **Scans** the GitHub repo a video is selling, or searches for it when the video only names it (the report then says "found by searching; check this is the one in the video"). Only a skill with no code and a clean scan gets the buttons. One that contains code gets a warning, no buttons, and a clone line to run by hand only if it was confirmed. If it fails the scan, the only message is "Skill may contain malware like behaviour, it is not safe to install". Skip sends nothing more. Adopt never installs anything: it replies "Ready to adopt:" with a strict `git clone` line you run yourself.

## How it fits together

```mermaid
flowchart LR
    U([You in Telegram]) -->|webhook| R[relay<br/>Deno Deploy, ~300 lines]
    R -->|owner only, rate-capped<br/>fire with JOB JSON| RT[Claude Code routine<br/>fresh VM per video]
    RT -->|prepare| P[reelay.cloud<br/>download, frames, transcript]
    P --> C[Claude<br/>repo search + scan]
    C -->|report| R
    R -->|reply + Adopt/Skip| U
```

The relay holds the bot token and is the only thing that talks to Telegram. The routine reaches the relay with a secret the cloud proxy injects, so neither the routine nor Claude ever sees it. Details: [docs/CLOUD-SPEC.md](docs/CLOUD-SPEC.md).

## Set it up (about 20 minutes)

Omnividens runs in the cloud, so you set up three small things: a Telegram bot, a free Deno relay, and a Claude Code routine. You type every secret yourself. Nothing here asks you to share a token with anyone.

### 1. What you need

- A **Telegram** account, to create your own bot with @BotFather.
- A **GitHub** account, and your own copy of this repository (fork or copy it; the relay deploys from it).
- A free **Deno Deploy** account (console.deno.com, sign in with GitHub).
- A **Claude** plan that includes Claude Code routines (claude.ai/code).
- **Cost:** the relay and database are free. **Every video you send is one routine run and counts against your Claude plan usage.** Plans have hourly and daily limits, so a run can be refused when you hit them.
- A terminal with `openssl` (to make random secrets). macOS has it.

### 2. Cloud path

1. **Create the Telegram bot.** In Telegram, message @BotFather, send `/newbot`, and follow the prompts. Copy the bot token it gives you into your notes. Never paste it into a chat or a file in the repo.
2. **Make two random secrets.** Run this twice in your own terminal and keep both outputs in your notes. The first is `WEBHOOK_SECRET`, the second is `RELAY_SECRET`.
   ```bash
   openssl rand -hex 32
   ```
3. **Deploy the relay** ([`relay/README.md`](relay/README.md) has the same steps).
   1. Go to console.deno.com, sign in with GitHub and create an organization.
   2. Click **+ New App** and pick your copy of this repository.
   3. Build config: Framework preset **No Preset**, no install or build command, Runtime **Dynamic**, Entrypoint `relay/main.ts`. Click **Create App**. The first build may fail because there is no database yet. That's fine.
   4. In the organization's **Databases**, provision a **Deno KV** database, **Assign** it to your app, wait for "Connected", then restart the build.
   5. Copy the app's **production** URL, like `https://YOUR-APP.deno.net`. Not the preview URL.
4. **Relay settings.** In your app, open Settings → Environment Variables and add each as a **Secret**:
   - `TELEGRAM_BOT_TOKEN`: from @BotFather.
   - `WEBHOOK_SECRET` and `RELAY_SECRET`: from step 2.
   - `PUBLIC_URL`: your production URL. It is not needed once a webhook exists, but **a new bot needs it**: opening the status page at this address is what sets Telegram's webhook, and only to this address.
   - `ROUTINE_FIRE_URL` and `ROUTINE_TOKEN`: you get these in step 6.
   - Leave `OWNER_TELEGRAM_ID` out for now (step 8).
   - Optional: `MAX_RUNS_PER_HOUR` (default 20), `MAX_REPORTS_PER_DAY` (default 30).
5. **Create the routine and its environment** at claude.ai/code/routines. The routine uses this repository, with the prompt from [`routine/PROMPT.md`](routine/PROMPT.md) and no connectors. *Not run live from a clean account: the claude.ai screens may be named differently. If something doesn't match, follow the on-screen labels.*
   - **Setup script** (paste into the environment's settings box, not your terminal; it must not depend on the repo and must exit 0). This is the same as [`routine/setup.sh`](routine/setup.sh), shortened:
     ```bash
     #!/bin/bash
     apt-get update -y || true
     apt-get install -y ffmpeg || true
     python3 -m pip install -q -U "yt-dlp[default,curl-cffi]" deno faster-whisper || true
     exit 0
     ```
   - **Network access: Custom**, keep the defaults, and add these hosts:
     - Your relay host (`YOUR-APP.deno.net`) and `api.telegram.org`.
     - TikTok: `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.tiktokcdn-us.com`, `*.tiktokcdn-eu.com`.
     - Instagram: `*.instagram.com`, `*.cdninstagram.com`, `*.fbcdn.net`. *These three are my best guess; the exact host list for Instagram was not checked against a live run. If a run fails with `host_not_allowed`, add the host it names.*
     - YouTube (only matters with the Mac worker, below): `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`.
     - Speech-to-text model download: `huggingface.co`, `*.hf.co`.
   - **Network secret:** type **Bearer**, allowed website = your relay host, header `Authorization`, prefix `Bearer`, value = your `RELAY_SECRET` alone. This is how the routine proves itself to the relay without Claude ever seeing the secret.
6. **Add the routine's API trigger.** Open the routine, choose **Add trigger → API**, copy the fire URL into the relay variable `ROUTINE_FIRE_URL` and the token into `ROUTINE_TOKEN` (the token is shown once). Save, and redeploy the relay so it picks them up.
7. **Open your relay page** (`https://YOUR-APP.deno.net`). It should say "Not connected yet" with only `OWNER_TELEGRAM_ID` missing, and it sets the Telegram webhook. If other names are listed, trigger a new build so the variables load.
8. **Claim the bot.** Send `/start` to your bot. It answers "Your Telegram ID is N". Put N into `OWNER_TELEGRAM_ID`, redeploy, and reload the relay page until it says **Connected ✓**. From now on only that Telegram account can use the bot; everyone else is ignored silently.
9. **Test it.**
   1. Send `/help`. You should get the command list (`/report owner/repo`, `/goals`, `/projects`, `/help`).
   2. Optional: tell it your goals and projects so reports can say where a tool fits. Send `/goals` or `/projects` followed by lines (see `/help`). They are stored in your Deno KV and read by the routine, so never put secrets in them.
   3. Send one **TikTok or Instagram link** to a video that names a GitHub repo.
   4. You should see "Got it. Takes a few minutes.", then one sentence on the tool with "This test will take approximately N minutes", then the report with tokens, projects, a rating out of 10 and, for a clean skill without code, Skip / Adopt. A video with no skill or tool gets "No skill or tool in this video." and a short summary. *This format has not run live yet.*
   - If nothing arrives after several minutes, check the routine's run log at claude.ai/code. A host the VM couldn't reach shows as `host_not_allowed`.

### 3. Optional: Mac worker (macOS only)

Only needed to get YouTube links through. It uses launchd, so it works on macOS only.

Follow [docs/SETUP-MAC-WORKER.md](docs/SETUP-MAC-WORKER.md). *Not run live yet: the plist generation was tested from a clean clone, but the worker has not fetched a real video on any Mac.*

### 4. Honest limits

- **YouTube is blocked from cloud IPs.** Without the Mac worker, a YouTube link gets a plain "blocked from the cloud" reply. Sending the video file (up to 20 MB) works.
- **Instagram may need a login** from cloud IPs, depending on the video. A reel ran end to end once; others may fail.
- **Usage limits.** Each video is one run on your Claude plan. When you hit your plan's limits, runs stop until they reset.
- **Adopt only prints a `git clone` line and installs nothing.** The cloud clones repos to read and scan them; nothing is run.
- The safety result means "found none of the patterns I check for", not "safe". The rating out of 10 is the model's opinion, and token numbers are approximate.

### 5. What was tested

Tested on the author's Mac and in the author's own cloud setup, with a live TikTok and one live Instagram reel. **No other person's laptop or account has been tried**, and these steps have not been followed from a clean account. Not run live: uploaded video files, the one-short-report-per-repo format with a skill that has no code, and the Mac worker's full path on someone else's machine. Local tests need no network or key:

```bash
pip install -e '.[dev]' && pytest -q
```

```bash
cd relay && deno task test
```

Local mode (`reelay setup`, `reelay run`) is the original design and still works, but it needs your computer on and a network that doesn't intercept Telegram's TLS.

## File map

| Path | What's in it |
|---|---|
| `relay/` | Deno relay: Telegram webhook, owner gate, dedupe, rate cap, routine trigger, `/api/*` for the routine (`app.ts`, tests in `app_test.ts`) |
| `routine/` | `PROMPT.md` (what the routine does, hard rules first) and `setup.sh` (ffmpeg, yt-dlp, faster-whisper) |
| `reelay/cloud/` | Routine side: `prepare` (job → video → `bundle.json`) and `report` (verdict → Telegram message + Adopt button) |
| `reelay/video/` | yt-dlp download, ffmpeg frames, transcript; ≤ 200 MB, ≤ 10 min, no private hosts |
| `reelay/factcheck/` | Fact-check instructions and strict JSON validation of the verdict |
| `reelay/sandbox/` | Repo fetch, static safety scan, isolated trial (local mode) |
| `reelay/profile/` | Quiz, vibe dial, local profile |
| `reelay/core/` | Shared types (`types.py`), TOML config, pipeline wiring |
| `reelay/bot/`, `router/`, `adapters/`, `installer/`, `terminal/` | Local mode: polling bot with 🧾 trust receipts, Haiku instruction router, Claude Code adapter, setup wizard |
| `tests/` | pytest suite, all fakes |
| `docs/` | Spec, architecture, security model, demo script, task board, peer reviews |

**Local mode** (`reelay setup` then `reelay run`) is the original design: the bot runs on your own Mac and can also take typed instructions for your Claude Code. It still works and is tested, but needs the computer on and a network that doesn't intercept Telegram's TLS.

## Safety model, in plain words

- **Video is data, never instructions.** Captions, transcripts and on-screen text are read, never obeyed. If a video tries to give orders, the report says so.
- **Only you can talk to your bot.** The relay checks your Telegram ID; strangers get no reply and cause no run.
- **Secrets stay out of the AI.** The bot token lives only in the relay; the relay secret is injected by the network proxy. The routine prompt forbids printing environment variables.
- **Nothing gets installed.** Repos are cloned into `/tmp/trial` and scanned, not run. Adopt only prints a `git clone -- https://github.com/owner/repo` line.
- **Locked-down network.** The routine's allowlist covers only the relay, Telegram, the video sites and the Whisper model host.

Full threat model, with the review finding behind each protection: [docs/SECURITY.md](docs/SECURITY.md).

## How it was built

An **Opus orchestrator** session wrote the spec, the shared contract and the task board ([docs/TASKS.md](docs/TASKS.md)), and merged only branches the owner approved. **Sonnet builder agents** each owned one task in its own git worktree, and a different agent wrote a **peer review** of each ([docs/reviews/](docs/reviews/)). Rules: [AGENTS.md](AGENTS.md). Those files are kept on purpose as a record of the process.

| Number | Value (from `git log` and the review files) |
|---|---|
| Commits on `main` | 127 over 8 days (28 Oct – 4 Nov 2026) |
| Merges of approved branches | 26 |
| Peer reviews | 20 files, one per task plus follow-ups |
| BLOCKERs caught before merge | 7 in the first 9 module reviews alone |
| Tests | 474 Python + 16 relay, no network or key needed |
| Code | ≈ 5,200 lines of Python in `reelay/`, ≈ 4,100 in `tests/`, ≈ 570 TypeScript in `relay/` |

What review caught, for example: the bot **downloaded files from strangers before checking pairing**; the sandbox's blanket `Read` rule would have let a malicious skill read `~/.ssh`; the Adopt message could have printed a `git clone --upload-pack=…` line built from a video-supplied URL.

**Why the cloud pivot:** some networks intercept TLS and re-sign it, so a bot running on a laptop behind one can't verify Telegram's certificate. Rather than disable certificate checks or route around the interception, the bot moved off the laptop entirely (see [docs/CLOUD-SPEC.md](docs/CLOUD-SPEC.md)).

## Limitations

- **What has run live:** a real TikTok went from Telegram through the relay and routine (download, frames, transcript, web-sourced fact-check) and the report came back to Telegram, about 3 minutes end to end (4 Nov 2026). An Instagram reel also ran end to end (5 Nov 2026). **Not yet run live:** uploaded video files, and the current two-message report format (with repo scan and Adopt).
- **Only the hosts you allow are reachable.** Add the TikTok and Instagram hosts as in the setup guide; X links are not covered, and any site may need a login from cloud IPs. Local mode tries links from your own network.
- **TikTok downloads rely on yt-dlp's browser impersonation** (`curl-cffi`) to get past a bot check, so they can break whenever TikTok changes it.
- **YouTube downloads from cloud IPs are unreliable** (YouTube blocks many datacenter IPs). Sending the video file works instead.
- **Slow and rationed.** A fresh VM per video means about 3 minutes per reply, and routine runs come out of the owner's Claude plan (capped at 30 runs/hour by the platform, lower by `MAX_RUNS_PER_HOUR`).
- **Repo trials are scan-and-read only** in the cloud. Local mode can run a trial in an isolated folder, but that is folder isolation, not a container.
- Instagram carousels/images fail with a "send a screen recording" message.

## Roadmap

1. Live run of the repo scan and Adopt / Skip, then a demo recording.
2. Typed instructions in cloud mode (local mode already has them).
3. Container trials that actually run a repo's quick-start with no secrets present.
4. Fit scoring against your real projects.

Spec: [docs/SPEC.md](docs/SPEC.md) · Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Demo script: [docs/DEMO.md](docs/DEMO.md)
