# Cloud setup checklist

You type every secret yourself. Claude never sees your bot token, relay secret or routine token.
About 15 minutes. Do the steps in order.

## Claude does (after you say yes)

- **A. GitHub repo.** Creates a **private** repo on your GitHub account and pushes this project to it.
- **B. Routine.** Creates the routine through the API: Sonnet, this repo, prompt = `routine/PROMPT.md`, no connectors.

## You do

1. **Make two random secrets.** Run this twice in your own Terminal and keep both outputs in Notes. Never paste them into chat.
   ```bash
   openssl rand -hex 32
   ```
   The first one is `WEBHOOK_SECRET`, the second one is `RELAY_SECRET`.
2. **Deploy the relay.** (Deno moved: the old dash.deno.com is gone.)
   - Go to console.deno.com, sign in with GitHub, and create an organization.
   - Click **+ New App** and pick the `omnividens` repo. If it isn't listed, use **Configure GitHub App permissions** to grant access.
   - Click **Edit build config**: Framework preset **No Preset**, leave Install and Build empty, Runtime **Dynamic**, Entrypoint `relay/main.ts`.
   - Click **Create App**. The first build may fail at Warm up because there is no database yet. That's fine.
   - Go to the organization's **Databases** → **Provision Database** → **Deno KV**, name it, save. Click **Assign** and pick your app. Wait for "Connected". Then restart the build.
   - Copy the app's production URL (`https://….deno.net`). Not the preview URL.
3. **Relay settings** (your app → Settings → Environment Variables). Add each as a **Secret**, with the Production and Development contexts on:
   - `TELEGRAM_BOT_TOKEN`: from @BotFather.
   - `WEBHOOK_SECRET` and `RELAY_SECRET`: from step 1.
   - `PUBLIC_URL`: the production URL from step 2, for example `https://my-app.deno.net`. The status page sets the Telegram webhook to this address only, so nobody else can re-point your bot.
   - Leave `OWNER_TELEGRAM_ID` out for now.
4. **Routine trigger.** Open the routine Claude made (claude.ai/code/routines), then **Add trigger → API**.
   - Copy the fire URL into the relay variable `ROUTINE_FIRE_URL`.
   - Copy the token into `ROUTINE_TOKEN`. The token is shown only once.
5. **Routine environment** (claude.ai/code → environment **Default**):
   - **Setup script:** paste this into the box in the settings page, not into your Terminal. It must not depend on the repo and must exit 0, or every session in this environment fails to start. The routine installs the repo's own package itself when it runs.
     ```bash
     #!/bin/bash
     apt-get update -y || true
     apt-get install -y ffmpeg || true
     python3 -m pip install -q -U "yt-dlp[default,curl-cffi]" deno faster-whisper || true
     exit 0
     ```
   - **Network access → Custom**, keep the defaults checked, and add: your relay host, `api.telegram.org`, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.tiktokcdn-us.com`, `*.tiktokcdn-eu.com`, `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `huggingface.co`, `*.hf.co`.
     - If a run says `host_not_allowed`, add the host it names.
     - Switching from Full to Custom also tightens what tools from videos can reach.
   - **Network secret:** type **Bearer**, allowed website = your relay host, header name `Authorization`, prefix `Bearer`, value = your `RELAY_SECRET` alone. Leave path prefixes empty.
6. **Connect.**
   - Stop any local copy of the bot first (`reelay service remove`, and quit any `reelay run`). One bot token can't do both.
   - Open your relay's production URL in a browser. It should say "Not connected yet", with only `OWNER_TELEGRAM_ID` missing. If other names are listed, trigger a new build so the variables load.
   - Send `/start` to your bot. It replies "Your Telegram ID is N".
   - Put N into `OWNER_TELEGRAM_ID`, save, and reload the page until it says "Connected ✓".
7. **Test.** Share a TikTok link with the bot.
   - You should get "Got it. Takes a few minutes." and then one report.
   - If YouTube links fail, send the video file instead.
   - Optional: tell the bot what you build so reports can say where a tool fits. Send `/projects` followed by one `Name: one line` per line (up to 20 lines, 200 characters each). `/projects` alone shows the list, `/projects clear` empties it. It is stored in Deno KV and read by the routine, so never put secrets in it.
   - Optional: tell the bot your goals so reports and the fact-check can use them. Send `/goals` followed by up to 5 lines (300 characters each). `/goals` alone shows them, `/goals clear` empties them. Same rule as `/projects`: stored in Deno KV and read by the routine, so never put secrets in it.
   - Reports left out (more than 9 finds, or the daily limit): send `/report owner/repo` for one. Each one starts a new run and uses your Claude usage. The exact repo you type counts as confirmed.
   - Optional relay env vars: `MAX_REPORTS_PER_DAY` (default 30) caps per-candidate reports a day.

## Optional: Mac fallback for YouTube (T25)

YouTube answers cloud downloads with "confirm you're not a bot". With this on, the relay hands those links to your Mac, which only fetches: it runs no model and no routine, and the video file never leaves the Mac (only small frames and the transcript go up). Skip it and blocked YouTube links just get a plain "blocked from the cloud" reply.

1. **Make a third secret** in your own Terminal and keep it in Notes. Never paste it into chat:
   ```bash
   openssl rand -hex 32
   ```
2. **Relay:** Deno console → your app → Settings → Environment Variables → add `MAC_SECRET` as a **Secret** with that value (Production and Development), then redeploy. It opens only the Mac worker routes; `RELAY_SECRET` does not open them.
3. **Mac:** add two lines to `~/.reelay/config.toml` (edit it yourself):
   ```toml
   relay_url = "https://YOUR-APP.deno.net"
   mac_secret = "the value from step 1"
   ```
   Then lock the file down: `chmod 600 ~/.reelay/config.toml`.
4. **Start it:** `reelay mac-worker install` (checks the relay once a minute while your Mac is awake and logged in; `reelay mac-worker remove` stops it). Needs `yt-dlp` and `ffmpeg` on the Mac. Optional: `faster-whisper` with its model already downloaded gives transcripts for videos without captions; nothing is ever sent to Groq or OpenAI.
5. **How it behaves:** one yt-dlp attempt per link, never retried. If the Mac is asleep the relay keeps the link for 24 hours (a claim lasts 15 minutes, two attempts), then tells you and asks you to resend. If the school network blocks or rate-limits the Mac too, you get that reason in Telegram.
6. Expiry messages are sent when anything next hits the relay (a message, a worker poll) and by a 10-minute cron if your Deno Deploy supports `Deno.cron`. Without cron an expiry message can arrive late. A queued job's database row is kept for 7 days in the database so the message still goes out the next time anything touches the relay.

## If something goes wrong

Tell Claude "check the last routine run". It can read the run log (never your secrets) and fix the code.
