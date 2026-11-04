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
     python3 -m pip install -q -U "yt-dlp[default]" deno faster-whisper || true
     exit 0
     ```
   - **Network access → Custom**, keep the defaults checked, and add: your relay host, `api.telegram.org`, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `huggingface.co`, `*.hf.co`.
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

## If something goes wrong

Tell Claude "check the last routine run". It can read the run log (never your secrets) and fix the code.
