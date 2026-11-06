# Omnividens cloud relay

A tiny web service that connects your Telegram bot to your Claude Code routine. It runs for free on
Deno Deploy, so your Mac does not need to be on.

## Deploy (about 10 minutes)

1. Go to <https://console.deno.com>, sign in with GitHub and create an organization.
2. Click **+ New App** and pick this repository.
3. In the build config choose **No Preset**, Runtime **Dynamic**, Entrypoint `relay/main.ts` (no
   install or build step). Then provision a **Deno KV** database under the organization's
   **Databases** and assign it to the app.
4. In the app settings, add these environment variables (names only here; you type the values):
   - `TELEGRAM_BOT_TOKEN` from @BotFather
   - `OWNER_TELEGRAM_ID` (leave empty at first, see step 6)
   - `WEBHOOK_SECRET` any long random string you invent
   - `RELAY_SECRET` another long random string; the routine uses it to call the relay
   - `PUBLIC_URL` this app's own https address (for example `https://my-app.deno.net`); the webhook is always set to it
   - `ROUTINE_FIRE_URL` and `ROUTINE_TOKEN` from your Claude Code routine's API trigger
   - `MAX_RUNS_PER_HOUR` optional, default 20
5. Open your app's production URL. The page shows **Connected ✓** once everything is set. If not, it
   lists the missing variable names (never the values). Opening the page also registers the Telegram
   webhook, and it is safe to reload.
6. No owner ID yet? Message your bot `/start`. It answers "Your Telegram ID is N." Put N in
   `OWNER_TELEGRAM_ID` and redeploy.

## Deno Deploy features used

- `Deno.serve` (HTTP handler)
- Deno KV (`Deno.openKv()`) for update de-duplication, the hourly rate counter, Adopt links and your
  profile.

KV is on the free plan (1 GiB) but must be provisioned and assigned to the app, or the app fails at
startup. It stores data in the US. It needs no extra flags on Deploy.

## Develop

```
cd relay
deno task test     # 16 tests, fake fetch + in-memory KV, no network
deno task check
deno task fmt
```

The binding contract is in `docs/CLOUD-SPEC.md`. The relay never logs tokens, message text or file
contents.
