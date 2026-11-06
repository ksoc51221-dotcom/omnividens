# Set up the Mac worker (optional)

YouTube often refuses downloads from the cloud ("confirm you're not a bot"). With the Mac worker on, your own Mac downloads those links instead and sends the relay small frames plus the transcript. The cloud then writes the report as usual.

You do every step yourself. The secret is never printed on screen and never pasted into a chat.

## What you need

- A Mac with the Omnividens relay already working (see the cloud setup guide) and its address, like `https://YOUR-APP.deno.net`.
- `reelay` installed on this Mac, plus `yt-dlp` and `ffmpeg`:
  ```bash
  brew install yt-dlp ffmpeg
  ```
- Optional: `faster-whisper` with its model already downloaded, for videos without captions. Without it those videos just get a report that says no captions were available.

## Steps

1. **Make the secret and copy it** without showing it. This puts it on your clipboard only:
   ```bash
   openssl rand -hex 32 | pbcopy
   ```
2. **Relay:** open the Deno console, your app, Settings, Environment Variables. Add `MAC_SECRET` as a **Secret** (Production and Development) and paste. Save and redeploy the app.
3. **Config on the Mac.** Create the file private, then open it:
   ```bash
   mkdir -p ~/.reelay && touch ~/.reelay/config.toml && chmod 600 ~/.reelay/config.toml
   open -e ~/.reelay/config.toml
   ```
   Add these two lines (keep any lines already there). Paste the secret from your clipboard between the quotes, by hand:
   ```toml
   relay_url = "https://YOUR-APP.deno.net"
   mac_secret = "PASTE HERE"
   ```
   Save, then clear your clipboard by copying any other text.
4. **Install.** Run this in a Terminal where `yt-dlp --version` and `ffmpeg -version` work (the worker remembers that Terminal's PATH):
   ```bash
   reelay mac-worker install
   ```
5. **Check it is running:**
   ```bash
   launchctl print gui/$(id -u)/com.reelay.macworker | head -5
   ```
   It shows a job in "not running" most of the time. That is normal: it wakes once a minute, checks the relay, and exits. To run one check by hand: `reelay mac-worker --once` (no output means nothing was waiting). Its log is `~/.reelay/macworker.log`; it never contains the secret.
6. **Test:** send the bot a YouTube link. If YouTube blocks the cloud you get a message saying your Mac is fetching it, and the report follows a few minutes later.

## Remove it

```bash
reelay mac-worker remove
```

This stops the worker and deletes its launch file. To fully revoke it, also delete `MAC_SECRET` in the Deno console and the two lines in your config.

## Limits

- macOS only. On other systems the install command prints a message and does nothing.
- The Mac must be awake and logged in. If it is asleep, the relay keeps the link for 24 hours and runs it when the Mac wakes. After that you are told and can resend.
- It only fetches. It runs no AI model and no routine: it downloads the video, picks up to 20 small frames, and uploads the frames and transcript. The video file stays on your Mac and is deleted afterwards.
- One attempt per link, no retries, no cookies, no proxy. If your own network blocks or rate-limits the download, you get that reason in Telegram.
- Transcripts come from the video's own captions, or from local Whisper if you installed it. Audio is never sent to any service.
