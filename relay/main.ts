import { makeHandler } from "./app.ts";

const kv = await Deno.openKv();
Deno.serve(makeHandler({
  env: (name) => Deno.env.get(name),
  kv,
  fetch,
  now: () => Date.now(),
  randomId: () => crypto.randomUUID().replaceAll("-", ""),
}));
