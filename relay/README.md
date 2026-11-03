# Reelay cloud relay

A tiny web service that connects your Telegram bot to your Claude Code routine. It runs for free on Deno Deploy, so your Mac does not need to be on.

## Deploy (about 10 minutes)

1. Go to <https://deno.com/deploy> and choose **Sign in with GitHub**.
2. Create a **new project** from this repository.
3. Set the **entrypoint** to `relay/main.ts` (no build step).
4. In the project settings, add these environment variables (names only here; you type the values):
   - `TELEGRAM_BOT_TOKEN` from @BotFather
   - `OWNER_TELEGRAM_ID` (leave empty at first, see step 6)
   - `WEBHOOK_SECRET` any long random string you invent
   - `RELAY_SECRET` another long random string; the routine uses it to call the relay
   - `ROUTINE_FIRE_URL` and `ROUTINE_TOKEN` from your Claude Code routine's API trigger
   - `MAX_RUNS_PER_HOUR` optional, default 20
5. Open your project URL. The page shows **Connected ✓** once everything is set. If not, it lists the missing variable names (never the values). Opening the page also registers the Telegram webhook, and it is safe to reload.
6. No owner ID yet? Message your bot `/start`. It answers "Your Telegram ID is N." Put N in `OWNER_TELEGRAM_ID` and redeploy.

## Deno Deploy features used

- `Deno.serve` (HTTP handler)
- Deno KV (`Deno.openKv()`) for update de-duplication, the hourly rate counter, Adopt links and your profile.

Verify that KV is enabled for your project on the free plan; if the dashboard asks you to create or attach a KV database, do that first. KV needs no extra flags on Deploy.

## Develop

```
cd relay
deno task test     # 16 tests, fake fetch + in-memory KV, no network
deno task check
deno task fmt
```

The binding contract is in `docs/CLOUD-SPEC.md`. The relay never logs tokens, message text or file contents.
