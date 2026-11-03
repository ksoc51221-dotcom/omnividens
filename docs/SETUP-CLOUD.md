# Cloud setup checklist

You type every secret yourself. Claude never sees or handles your bot token, relay secret or routine token. Do these in order.

1. **GitHub repo.** Create a new repo on github.com (private is fine) and push this project to it.
2. **Deploy the relay.** Go to Deno Deploy, sign in with GitHub, create a project from your repo, and follow `relay/README.md` (entry file and steps are there). Note the relay URL it gives you.
3. **Relay env vars** (Deno Deploy dashboard, you type them): `TELEGRAM_BOT_TOKEN` (from @BotFather), `WEBHOOK_SECRET` (any long random string), `RELAY_SECRET` (another long random string). Leave `OWNER_TELEGRAM_ID` empty for now. Optional: `MAX_RUNS_PER_HOUR`.
4. **Create the routine** (Claude Code on the web, Routines): repo = your repo, model = Sonnet, prompt = the contents of `routine/PROMPT.md`, trigger = API. Copy the fire URL and the token it shows. Put them in the relay env vars `ROUTINE_FIRE_URL` and `ROUTINE_TOKEN`.
5. **Routine environment.** Network access = Custom with this allowlist: `api.telegram.org`, your relay host, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokv.com`, `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `github.com`, `codeload.github.com`, `pypi.org`, `files.pythonhosted.org`. Uncertain (add if setup or a download fails): `deno.land`, `dl.deno.land`, `github.com` release downloads (`objects.githubusercontent.com`), `archive.ubuntu.com` and `security.ubuntu.com` (for ffmpeg via apt, may already be preinstalled). Setup script: `bash routine/setup.sh`.
6. **Network secret.** In the same environment add a secret for your relay host: header `Authorization` = `Bearer <your RELAY_SECRET>`. The proxy adds it on calls to the relay, so the VM never holds it.
7. **Check the relay.** Open the relay URL in a browser. It should say "Connected". If it lists missing env vars, add them.
8. **Find your Telegram ID.** Message your bot `/start`. It answers "Your Telegram ID is N". Put N in the relay env var `OWNER_TELEGRAM_ID` and save.
9. **Test.** Share a TikTok link to the bot. You should get "Got it. Takes a few minutes." and then one report. YouTube may be blocked from cloud IPs; if so, send the video file instead.

Local test (optional): `RELAY_SECRET` set in your own shell makes the client add the header itself. Never paste it into chat.
