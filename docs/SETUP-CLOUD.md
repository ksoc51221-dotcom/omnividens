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
2. **Deploy the relay.**
   - Go to dash.deno.com and click **Sign in with GitHub**.
   - Click **New Project**, pick the repo from step A, and set the entrypoint to `relay/main.ts`.
   - Copy the URL it gives you (`https://….deno.dev`).
   - Check that KV is available on your plan; see `relay/README.md`.
3. **Relay settings** (Deno Deploy → your project → Settings → Environment Variables):
   - `TELEGRAM_BOT_TOKEN`: from @BotFather.
   - `WEBHOOK_SECRET` and `RELAY_SECRET`: from step 1.
   - Leave `OWNER_TELEGRAM_ID` empty for now.
4. **Routine trigger.** Open the routine Claude made (claude.ai/code/routines), then **Add trigger → API**.
   - Copy the fire URL into the relay variable `ROUTINE_FIRE_URL`.
   - Copy the token into `ROUTINE_TOKEN`. The token is shown only once.
5. **Routine environment** (claude.ai/code → environment **Default**):
   - **Setup script:** `bash routine/setup.sh`.
   - **Network access → Custom**, keep the defaults checked, and add: your relay host, `api.telegram.org`, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `huggingface.co`, `*.hf.co`.
     - If a run says `host_not_allowed`, add the host it names.
     - Switching from Full to Custom also tightens what tools from videos can reach.
   - **Network secret:** host = your relay host, header `Authorization`, value `Bearer ` + your `RELAY_SECRET`.
6. **Connect.**
   - Open your relay URL in a browser and check that it says "Connected".
   - Send `/start` to your bot. It replies "Your Telegram ID is N".
   - Put N into `OWNER_TELEGRAM_ID` and save.
7. **Test.** Share a TikTok link with the bot.
   - You should get "Got it. Takes a few minutes." and then one report.
   - If YouTube links fail, send the video file instead.

## If something goes wrong

Tell Claude "check the last routine run". It can read the run log (never your secrets) and fix the code.
