# Cloud setup checklist

You type every secret yourself. Claude never sees your bot token, relay secret or routine token.
About 15 minutes. Do the steps in order.

## Before you start

Read the step-by-step guide in the README first (`README.md`, "Set it up"). This page is the longer checklist. Where it says "the routine Claude made", you can also create the routine yourself at claude.ai/code/routines: this repository, the prompt from `routine/PROMPT.md`, no connectors. Not run live from a clean account.

## You do

1. **Make two random secrets.** Run this twice in your own Terminal and keep both outputs in Notes. Never paste them into chat.
   ```bash
   openssl rand -hex 32
   ```
   The first one is `WEBHOOK_SECRET`, the second one is `RELAY_SECRET`.
2. **Deploy the relay.** (Deno moved: the old dash.deno.com is gone.)
   - Go to console.deno.com, sign in with GitHub, and create an organization.
   - Click **+ New App** and pick your copy of this repository. If it isn't listed, use **Configure GitHub App permissions** to grant access.
   - Click **Edit build config**: Framework preset **No Preset**, leave Install and Build empty, Runtime **Dynamic**, Entrypoint `relay/main.ts`.
   - Click **Create App**. The first build may fail at Warm up because there is no database yet. That's fine.
   - Go to the organization's **Databases** → **Provision Database** → **Deno KV**, name it, save. Click **Assign** and pick your app. Wait for "Connected". Then restart the build.
   - Copy the app's production URL (`https://….deno.net`). Not the preview URL.
3. **Relay settings** (your app → Settings → Environment Variables). Add each as a **Secret**, with the Production and Development contexts on:
   - `TELEGRAM_BOT_TOKEN`: from @BotFather.
   - `WEBHOOK_SECRET` and `RELAY_SECRET`: from step 1.
   - `PUBLIC_URL`: the production URL from step 2, for example `https://my-app.deno.net`. Opening the status page at this address sets the Telegram webhook to it; opening any other address changes nothing. Without it the page changes nothing, so an existing bot keeps working, but a new bot gets no webhook until you add it and open the page once.
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
   - **Network access → Custom**, keep the defaults checked, and add: your relay host, `api.telegram.org`, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.tiktokcdn-us.com`, `*.tiktokcdn-eu.com`, `*.instagram.com`, `*.cdninstagram.com`, `*.fbcdn.net` (Instagram hosts are a best guess, not checked against a live run), `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `huggingface.co`, `*.hf.co`.
     - If a run says `host_not_allowed`, add the host it names.
     - Switching from Full to Custom also tightens what tools from videos can reach.
   - **Network secret:** type **Bearer**, allowed website = your relay host, header name `Authorization`, prefix `Bearer`, value = your `RELAY_SECRET` alone. Leave path prefixes empty.
6. **Connect.**
   - Stop any local copy of the bot first (`reelay service remove`, and quit any `reelay run`). One bot token can't do both.
   - Open your relay's production URL in a browser. It should say "Not connected yet", with only `OWNER_TELEGRAM_ID` missing. If other names are listed, trigger a new build so the variables load.
   - Send `/start` to your bot. It replies "Your Telegram ID is N".
   - Put N into `OWNER_TELEGRAM_ID`, save, and reload the page until it says "Connected ✓".
7. **Test.** Send `/help`, then share a TikTok or Instagram link to a video that names a GitHub repo.
   - You should get "Got it. Takes a few minutes.", a short summary, then one short report per repo.
   - If YouTube links fail, send the video file instead.
   - Optional: tell the bot what you build so reports can say where a tool fits. Send `/projects` followed by one `Name: one line` per line (up to 20 lines, 200 characters each). `/projects` alone shows the list, `/projects clear` empties it. It is stored in Deno KV and read by the routine, so never put secrets in it.
   - Optional: tell the bot your goals so reports and the fact-check can use them. Send `/goals` followed by up to 5 lines (300 characters each). `/goals` alone shows them, `/goals clear` empties them. Same rule as `/projects`: stored in Deno KV and read by the routine, so never put secrets in it.
   - Reports left out (more than 9 finds, or the daily limit): send `/report owner/repo` for one. Each one starts a new run and uses your Claude usage. The exact repo you type counts as confirmed.
   - Optional relay env vars: `MAX_REPORTS_PER_DAY` (default 30) caps per-candidate reports a day.

## Optional: Mac fallback for YouTube (macOS only, uses launchd)

YouTube answers cloud downloads with "confirm you're not a bot". With this on, the relay hands those links to your Mac, which only fetches: it runs no model and no routine, and the video file never leaves the Mac (only small frames and the transcript go up). Skip it and blocked YouTube links just get a plain "blocked from the cloud" reply.

Setup steps are in [SETUP-MAC-WORKER.md](SETUP-MAC-WORKER.md).

## If something goes wrong

Open the routine's run log at claude.ai/code. A host the VM couldn't reach shows as `host_not_allowed`: add it to the network list.
