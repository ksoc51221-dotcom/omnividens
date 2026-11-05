import { makeHandler } from "./app.ts";

const kv = await Deno.openKv();
const handler = makeHandler({
  env: (name) => Deno.env.get(name),
  kv,
  fetch,
  now: () => Date.now(),
  randomId: () => crypto.randomUUID().replaceAll("-", ""),
});
Deno.serve(handler);

// Expiry messages also run from cron where the platform supports it (Deno.cron is unstable);
// otherwise they run on every webhook and every Mac poll.
try {
  if (typeof Deno.cron === "function") Deno.cron("mac-sweep", "*/10 * * * *", () => handler.sweep());
} catch { /* no cron here */ }
