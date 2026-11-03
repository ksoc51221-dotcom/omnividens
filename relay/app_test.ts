import { makeHandler } from "./app.ts";

const ENV: Record<string, string> = {
  TELEGRAM_BOT_TOKEN: "TOKEN123",
  OWNER_TELEGRAM_ID: "42",
  WEBHOOK_SECRET: "whsec",
  RELAY_SECRET: "relsec",
  ROUTINE_FIRE_URL: "https://fire.example/run",
  ROUTINE_TOKEN: "rtok",
};

interface Call {
  url: string;
  init?: RequestInit;
  // deno-lint-ignore no-explicit-any
  body?: any;
}

async function setup(env: Record<string, string> = ENV, fire = 200) {
  const kv = await Deno.openKv(":memory:");
  const calls: Call[] = [];
  let ids = 0;
  const fakeFetch = (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, init, body });
    if (url === env.ROUTINE_FIRE_URL) {
      return Promise.resolve(new Response("SECRET BODY", { status: fire }));
    }
    if (url.endsWith("/getFile")) {
      const size = body.file_id === "big" ? 30_000_000 : 5;
      return Promise.resolve(
        Response.json({ ok: true, result: { file_path: "v/a.mp4", file_size: size } }),
      );
    }
    if (url.includes("/file/bot")) return Promise.resolve(new Response("VIDEOBYTES"));
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const handler = makeHandler({
    env: (n) => env[n],
    kv,
    fetch: fakeFetch as typeof fetch,
    now: () => 3600000 * 100,
    randomId: () => `id${++ids}`,
  });
  const hook = (update: unknown, secret = "whsec") =>
    handler(
      new Request("https://relay.example/telegram", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": secret },
        body: JSON.stringify(update),
      }),
    );
  const api = (path: string, method = "GET", body?: unknown, auth = "Bearer relsec") =>
    handler(
      new Request(`https://relay.example${path}`, {
        method,
        headers: { authorization: auth },
        body: body === undefined
          ? undefined
          : typeof body === "string"
          ? body
          : JSON.stringify(body),
      }),
    );
  const sent = () => calls.filter((c) => c.url.endsWith("/sendMessage")).map((c) => c.body.text);
  return { handler, kv, calls, hook, api, sent, done: () => kv.close() };
}

const msg = (id: number, from: number, extra: object = { text: "hi" }) => ({
  update_id: id,
  message: { message_id: 7, from: { id: from }, chat: { id: from }, ...extra },
});

Deno.test("wrong webhook secret gives 401", async () => {
  const t = await setup();
  assertEq((await t.hook(msg(1, 42), "nope")).status, 401);
  assertEq(t.calls.length, 0);
  t.done();
});

Deno.test("duplicate update_id is processed once", async () => {
  const t = await setup();
  await t.hook(msg(1, 42));
  await t.hook(msg(1, 42));
  assertEq(t.sent().length, 1);
  t.done();
});

Deno.test("stranger is ignored with 200", async () => {
  const t = await setup();
  assertEq((await t.hook(msg(1, 999))).status, 200);
  assertEq(t.calls.length, 0);
  t.done();
});

Deno.test("owner unset: /start gets ID hint, other text ignored", async () => {
  const { OWNER_TELEGRAM_ID: _, ...env } = ENV;
  const t = await setup(env);
  await t.hook(msg(1, 77, { text: "hello" }));
  assertEq(t.calls.length, 0);
  await t.hook(msg(2, 77, { text: "/start" }));
  assertEq(t.sent(), ["Your Telegram ID is 77. Put it in OWNER_TELEGRAM_ID."]);
  t.done();
});

Deno.test("rate cap replies Busy and does not fire", async () => {
  const t = await setup({ ...ENV, MAX_RUNS_PER_HOUR: "2" });
  for (let i = 1; i <= 3; i++) await t.hook(msg(i, 42));
  assertEq(t.calls.filter((c) => c.url === ENV.ROUTINE_FIRE_URL).length, 2);
  assertEq(t.sent().at(-1), "Busy, try again later");
  t.done();
});

Deno.test("fire request has exact headers and body shape", async () => {
  const t = await setup();
  await t.hook(msg(5, 42, {
    caption: "look",
    video: { file_id: "vid", file_size: 100, mime_type: "video/mp4" },
    document: { file_id: "doc", file_size: 900, mime_type: "video/mp4" },
  }));
  assertEq(t.sent()[0], "Got it. Takes a few minutes.");
  const fire = t.calls.find((c) => c.url === ENV.ROUTINE_FIRE_URL)!;
  const h = fire.init!.headers as Record<string, string>;
  assertEq(h["Authorization"], "Bearer rtok");
  assertEq(h["anthropic-beta"], "experimental-cc-routine-2026-04-01");
  assertEq(h["content-type"], "application/json");
  assertEq(JSON.parse(fire.body.text), {
    v: 1,
    update_id: 5,
    chat_id: 42,
    message_id: 7,
    text: "look",
    file: { file_id: "doc", file_size: 900, mime_type: "video/mp4" },
    relay_url: "https://relay.example",
  });
  t.done();
});

Deno.test("text is capped at 4000 chars", async () => {
  const t = await setup();
  await t.hook(msg(1, 42, { text: "x".repeat(5000) }));
  const fire = t.calls.find((c) => c.url === ENV.ROUTINE_FIRE_URL)!;
  assertEq(JSON.parse(fire.body.text).text.length, 4000);
  t.done();
});

Deno.test("fire failure tells owner status code only", async () => {
  const t = await setup(ENV, 500);
  await t.hook(msg(1, 42));
  const last = t.sent().at(-1)!;
  assertEq(last.includes("500"), true);
  assertEq(last.includes("SECRET BODY"), false);
  t.done();
});

Deno.test("adopt and skip callbacks", async () => {
  const t = await setup();
  await t.api("/api/send", "POST", {
    chat_id: 42,
    text: "report",
    adopt_line: "git clone -- https://github.com/o/r",
  });
  const kb = t.calls.find((c) => c.body?.reply_markup)!.body.reply_markup.inline_keyboard[0];
  // deno-lint-ignore no-explicit-any
  assertEq(kb.map((b: any) => b.callback_data), ["adopt:id1", "skip:id1"]);
  const cb = (id: number, data: string) => ({
    update_id: id,
    callback_query: { id: "cq", from: { id: 42 }, data, message: { chat: { id: 42 } } },
  });
  await t.hook(cb(1, "adopt:id1"));
  await t.hook(cb(2, "skip:id1"));
  assertEq(t.sent().slice(-2), ["git clone -- https://github.com/o/r", "Skipped."]);
  assertEq(t.calls.filter((c) => c.url.endsWith("/answerCallbackQuery")).length, 2);
  t.done();
});

Deno.test("api requires bearer secret", async () => {
  const t = await setup();
  assertEq((await t.api("/api/profile", "GET", undefined, "Bearer wrong")).status, 401);
  assertEq((await t.api("/api/send", "POST", {}, "")).status, 401);
  assertEq((await t.api("/api/file/abc", "GET", undefined, "Bearer wrong")).status, 401);
  t.done();
});

Deno.test("adopt_line regex rejects bad lines", async () => {
  const t = await setup();
  for (
    const bad of [
      "git clone https://github.com/o/r",
      "git clone -- https://evil.com/o/r",
      "git clone -- https://github.com/o/r; rm -rf ~",
      "git clone -- https://github.com/o/r\nls",
    ]
  ) {
    const r = await t.api("/api/send", "POST", { chat_id: 1, text: "x", adopt_line: bad });
    assertEq(r.status, 400);
  }
  assertEq(t.calls.length, 0);
  t.done();
});

Deno.test("send splits at 4096, plain text, buttons on last part", async () => {
  const t = await setup();
  const r = await t.api("/api/send", "POST", {
    chat_id: 1,
    text: "a".repeat(9000),
    adopt_line: "git clone -- https://github.com/o/r",
  });
  assertEq(r.status, 200);
  const sends = t.calls.filter((c) => c.url.endsWith("/sendMessage"));
  assertEq(sends.map((c) => c.body.text.length), [4096, 4096, 808]);
  assertEq(sends.some((c) => "parse_mode" in c.body), false);
  assertEq(sends.map((c) => !!c.body.reply_markup), [false, false, true]);
  t.done();
});

Deno.test("file: streams small, refuses over 20MB, validates id", async () => {
  const t = await setup();
  const ok = await t.api("/api/file/small-1_A");
  assertEq(await ok.text(), "VIDEOBYTES");
  assertEq(t.calls.at(-1)!.url, "https://api.telegram.org/file/botTOKEN123/v/a.mp4");
  assertEq((await t.api("/api/file/big")).status, 413);
  assertEq((await t.api("/api/file/a..b%2F")).status, 400);
  t.done();
});

Deno.test("profile: roundtrip, size cap, object only", async () => {
  const t = await setup();
  assertEq(await (await t.api("/api/profile")).json(), {});
  assertEq((await t.api("/api/profile", "PUT", { a: 1 })).status, 200);
  assertEq(await (await t.api("/api/profile")).json(), { a: 1 });
  assertEq((await t.api("/api/profile", "PUT", { a: "x".repeat(70000) })).status, 413);
  assertEq((await t.api("/api/profile", "PUT", "[1]")).status, 400);
  assertEq((await t.api("/api/profile", "PUT", "nope")).status, 400);
  assertEq((await t.api("/api/profile", "DELETE")).status, 405);
  t.done();
});

Deno.test("status page hides values, lists missing names, sets webhook", async () => {
  const t = await setup();
  const ok = await (await t.handler(new Request("https://relay.example/"))).text();
  assertEq(ok.includes("Connected ✓"), true);
  for (const v of Object.values(ENV)) assertEq(ok.includes(v), false);
  const hook = t.calls.find((c) => c.url.endsWith("/setWebhook"))!.body;
  assertEq(hook.url, "https://relay.example/telegram");
  assertEq(hook.allowed_updates, ["message", "callback_query"]);
  t.done();

  const { RELAY_SECRET: _, ...env } = ENV;
  const t2 = await setup(env);
  const page = await (await t2.handler(new Request("https://relay.example/"))).text();
  assertEq(page.includes("RELAY_SECRET"), true);
  assertEq(page.includes("Connected ✓"), false);
  assertEq(page.includes("whsec"), false);
  t2.done();
});

Deno.test("unknown path 404, wrong method 405", async () => {
  const t = await setup();
  assertEq((await t.handler(new Request("https://relay.example/nope"))).status, 404);
  assertEq((await t.handler(new Request("https://relay.example/telegram"))).status, 405);
  const post = new Request("https://relay.example/", { method: "POST" });
  assertEq((await t.handler(post)).status, 405);
  t.done();
});

function assertEq(a: unknown, b: unknown) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}
