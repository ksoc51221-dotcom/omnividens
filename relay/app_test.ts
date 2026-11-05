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
  const clock = { t: 3600000 * 100 };
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
    now: () => clock.t,
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
  const mac = (path: string, method = "GET", body?: unknown, auth = "Bearer macsec") =>
    api(path, method, body, auth);
  const sent = () => calls.filter((c) => c.url.endsWith("/sendMessage")).map((c) => c.body.text);
  return { handler, kv, calls, hook, api, sent, clock, mac, done: () => kv.close() };
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
  assertEq(h["anthropic-version"], "2023-06-01");
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
  assertEq(await (await t.api("/api/profile")).json(), { projects: [] });
  assertEq((await t.api("/api/profile", "PUT", { a: 1 })).status, 200);
  assertEq(await (await t.api("/api/profile")).json(), { a: 1, projects: [] });
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

Deno.test("/projects: save, show, clear, caps, no routine fire", async () => {
  const t = await setup();
  await t.hook(msg(1, 42, { text: "/projects" }));
  assertEq(t.sent().at(-1)!.startsWith("No project list yet"), true);
  await t.hook(msg(2, 42, { text: "/projects\nOpenPlan: my planner\nSpool: screen recorder" }));
  assertEq(t.sent().at(-1), "Saved 2 projects.");
  await t.hook(msg(3, 42, { text: "/projects" }));
  assertEq(t.sent().at(-1), "Your projects (2):\nOpenPlan: my planner\nSpool: screen recorder");
  assertEq((await (await t.api("/api/profile")).json()).projects, [
    "OpenPlan: my planner",
    "Spool: screen recorder",
  ]);
  await t.hook(msg(4, 42, { text: "/projects " + "x".repeat(201) }));
  assertEq(t.sent().at(-1)!.startsWith("Not saved"), true);
  await t.hook(msg(5, 42, { text: "/projects\n" + Array(21).fill("a: b").join("\n") }));
  assertEq(t.sent().at(-1)!.startsWith("Not saved"), true);
  assertEq((await (await t.api("/api/profile")).json()).projects.length, 2); // nothing saved on reject
  await t.hook(msg(6, 42, { text: "/projects clear" }));
  assertEq((await (await t.api("/api/profile")).json()).projects, []);
  assertEq(t.calls.filter((c) => c.url === ENV.ROUTINE_FIRE_URL).length, 0);
  assertEq(t.sent().includes("Got it. Takes a few minutes."), false);
  t.done();
});

Deno.test("/projects from a stranger is ignored", async () => {
  const t = await setup();
  await t.hook(msg(1, 999, { text: "/projects\nEvil: x" }));
  assertEq(t.calls.length, 0);
  assertEq((await (await t.api("/api/profile")).json()).projects, []);
  t.done();
});

Deno.test("PUT /api/profile ignores a projects field and keeps the list", async () => {
  const t = await setup();
  await t.hook(msg(1, 42, { text: "/projects\nA: b" }));
  await t.api("/api/profile", "PUT", { vibe: "hype", projects: ["Hacked: yes"] });
  const got = await (await t.api("/api/profile")).json();
  assertEq(got.projects, ["A: b"]);
  assertEq(got.vibe, "hype");
  t.done();
});

Deno.test("report sends get their own buttons and obey the daily budget", async () => {
  const t = await setup({ ...ENV, MAX_REPORTS_PER_DAY: "2" });
  const line = (r: string) => ({
    chat_id: 42,
    text: "r",
    adopt_line: `git clone -- https://github.com/o/${r}`,
    report: true,
  });
  assertEq((await t.api("/api/send", "POST", line("a"))).status, 200);
  assertEq((await t.api("/api/send", "POST", line("b"))).status, 200);
  assertEq((await t.api("/api/send", "POST", line("c"))).status, 429);
  // two reports -> two different ids, so one tap cannot touch the other
  const ids = t.calls.filter((c) => c.url.endsWith("/sendMessage")).map((c) =>
    c.body.reply_markup.inline_keyboard[0][0].callback_data
  );
  assertEq(ids, ["adopt:id1", "adopt:id2"]);
  await t.hook({
    update_id: 9,
    callback_query: { id: "q", from: { id: 42 }, data: "adopt:id2", message: { chat: { id: 42 } } },
  });
  assertEq(t.sent().at(-1), "git clone -- https://github.com/o/b");
  t.done();
});

Deno.test("/report owner/repo fires one run; a bad /report does not", async () => {
  const t = await setup();
  await t.hook(msg(1, 42, { text: "/report foo/bar" }));
  assertEq(t.calls.filter((c) => c.url === ENV.ROUTINE_FIRE_URL).length, 1);
  await t.hook(msg(2, 42, { text: "/report" }));
  await t.hook(msg(3, 42, { text: "/report https://evil.sh/x; rm -rf" }));
  await t.hook(msg(4, 42, { text: "/report a/b c/d" }));
  assertEq(t.calls.filter((c) => c.url === ENV.ROUTINE_FIRE_URL).length, 1);
  assertEq(t.sent().filter((x) => x.startsWith("Send /report owner/repo")).length, 3);
  t.done();
});

Deno.test("adopt note is shown above the clone line; ADOPT_RE is strict", async () => {
  const t = await setup();
  const line = "git clone -- https://github.com/o/r";
  const r = await t.api("/api/send", "POST", {
    chat_id: 42,
    text: "r",
    adopt_line: line,
    adopt_note: "⚠️ code\x1b[31m here\nI have NOT installed anything.‮",
  });
  assertEq(r.status, 200);
  await t.hook({
    update_id: 9,
    callback_query: { id: "q", from: { id: 42 }, data: "adopt:id1", message: { chat: { id: 42 } } },
  });
  assertEq(t.sent().at(-1), `⚠️ code [31m here\nI have NOT installed anything. \n\n${line}`);
  for (
    const bad of [
      "git clone -- https://github.com/../r",
      "git clone -- https://github.com/o/..",
      "git clone -- https://github.com/-o/r",
      "git clone -- https://github.com/o_x/r",
    ]
  ) {
    assertEq(
      (await t.api("/api/send", "POST", { chat_id: 42, text: "r", adopt_line: bad })).status,
      400,
    );
  }
  t.done();
});

// ---- Mac fallback ----
const MENV = { ...ENV, MAC_SECRET: "macsec" };
const JOB = {
  v: 1,
  update_id: 50,
  chat_id: 42,
  message_id: 7,
  text: "see https://youtu.be/abc please",
  file: null,
  relay_url: "https://relay.example",
};
const jpeg = (n = 1000) => {
  const b = new Uint8Array(n);
  b.set([0xff, 0xd8, 0xff]);
  return btoa(String.fromCharCode(...b));
};
const bundleBody = (u: number, lease: string, frames = 2, extra: object = {}) => ({
  update_id: u,
  lease,
  meta: { duration_s: 30, transcript: "[0:01] hi", caption: "cap", ...extra },
  frames: Array.from({ length: frames }, (_, i) => ({ time_s: i * 2, jpeg_b64: jpeg() })),
});
const fires = (t: Awaited<ReturnType<typeof setup>>) =>
  t.calls.filter((c) => c.url === ENV.ROUTINE_FIRE_URL);

Deno.test("secret scoping: MAC_SECRET only opens /api/mac, RELAY_SECRET never does", async () => {
  const t = await setup(MENV);
  assertEq((await t.mac("/api/mac/next", "GET", undefined, "Bearer relsec")).status, 401);
  assertEq((await t.mac("/api/mac/next", "GET", undefined, "")).status, 401);
  for (const p of ["/api/profile", "/api/file/x", "/api/defer", "/api/bundle/abc"]) {
    assertEq((await t.api(p, "GET", undefined, "Bearer macsec")).status, 401);
  }
  assertEq(
    (await t.api("/api/send", "POST", { chat_id: 42, text: "x" }, "Bearer macsec")).status,
    401,
  );
  assertEq((await t.mac("/api/mac/next")).status, 204);
  t.done();
});

Deno.test("MAC_SECRET unset: worker routes closed, defer says not queued", async () => {
  const t = await setup();
  assertEq((await t.mac("/api/mac/next", "GET", undefined, "Bearer ")).status, 401);
  assertEq((await t.mac("/api/mac/next", "GET", undefined, "Bearer undefined")).status, 401);
  const r = await (await t.api("/api/defer", "POST", { job: JOB })).json();
  assertEq(r.queued, false);
  t.done();
});

Deno.test("defer: Mac never seen -> offline message; seen <3 min -> online message", async () => {
  const t = await setup(MENV);
  assertEq((await t.api("/api/defer", "POST", { job: JOB })).status, 200);
  assertEq(t.sent().length, 1);
  if (!t.sent()[0].includes("isn't online (last seen never)")) throw new Error(t.sent()[0]);
  await t.mac("/api/mac/next"); // heartbeat
  await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 51 } });
  assertEq(
    t.sent()[1],
    "YouTube blocks downloads from the cloud, so your Mac is fetching this one. Your report follows in a few minutes.",
  );
  t.clock.t += 4 * 60 * 1000; // heartbeat is now stale
  await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 52 } });
  if (!/last seen \d{4}-\d\d-\d\d \d\d:\d\d UTC/.test(t.sent()[2])) throw new Error(t.sent()[2]);
  t.done();
});

Deno.test("defer: one queued job per update_id, no loops, owner only", async () => {
  const t = await setup(MENV);
  assertEq((await (await t.api("/api/defer", "POST", { job: JOB })).json()).queued, true);
  const again = await (await t.api("/api/defer", "POST", { job: JOB })).json();
  assertEq(again.duplicate, true);
  assertEq(t.sent().length, 1); // told once
  assertEq((await t.api("/api/defer", "POST", { job: { ...JOB, bundle_id: "b1" } })).status, 400);
  assertEq(
    (await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 9, chat_id: 7 } })).status,
    403,
  );
  assertEq(
    (await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 9, file: { file_id: "f" } } }))
      .status,
    400,
  );
  t.done();
});

Deno.test("lease: claimed job is hidden for 15 min, comes back, gives up after two attempts", async () => {
  const t = await setup(MENV);
  await t.api("/api/defer", "POST", { job: JOB });
  const first = await (await t.mac("/api/mac/next")).json();
  assertEq([first.update_id, first.attempt], [50, 1]);
  assertEq((await t.mac("/api/mac/next")).status, 204); // leased
  t.clock.t += 16 * 60 * 1000; // Mac slept
  const second = await (await t.mac("/api/mac/next")).json();
  assertEq(second.attempt, 2);
  if (second.lease === first.lease) throw new Error("lease not renewed");
  // The old lease can no longer upload.
  assertEq((await t.mac("/api/mac/bundle", "POST", bundleBody(50, first.lease))).status, 409);
  t.clock.t += 16 * 60 * 1000;
  assertEq((await t.mac("/api/mac/next")).status, 204); // gave up
  if (
    !t.sent().at(-1)!.includes("https://youtu.be/abc") ||
    !t.sent().at(-1)!.includes("never finished")
  ) {
    throw new Error(t.sent().at(-1));
  }
  assertEq((await t.mac("/api/mac/next")).status, 204);
  assertEq(fires(t).length, 0);
  t.done();
});

Deno.test("oldest job is claimed first", async () => {
  const t = await setup(MENV);
  await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 70 } });
  await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 60 } });
  assertEq((await (await t.mac("/api/mac/next")).json()).update_id, 60);
  assertEq((await (await t.mac("/api/mac/next")).json()).update_id, 70);
  t.done();
});

Deno.test("bundle: stored per frame, fires routine with bundle_id, routine can read it back", async () => {
  const t = await setup(MENV);
  await t.api("/api/defer", "POST", { job: JOB });
  const c = await (await t.mac("/api/mac/next")).json();
  const r = await t.mac("/api/mac/bundle", "POST", bundleBody(50, c.lease, 3));
  assertEq(r.status, 200);
  assertEq(fires(t).length, 1);
  const sentJob = JSON.parse(fires(t)[0].body.text);
  assertEq(sentJob.update_id, 50);
  assertEq(sentJob.text, JOB.text);
  const id = sentJob.bundle_id;
  const got = await (await t.api("/api/bundle/" + id)).json();
  assertEq(got.frames.length, 3);
  assertEq(got.meta.transcript, "[0:01] hi");
  assertEq((await t.api("/api/bundle/" + id, "GET", undefined, "Bearer macsec")).status, 401);
  assertEq((await t.api("/api/bundle/nope")).status, 404);
  assertEq((await t.mac("/api/mac/next")).status, 204); // job finished and removed
  // each frame is its own KV value, well under 64 KiB
  const e = await t.kv.get<Uint8Array>(["mac", "bundle", id, "frame", 2]);
  assertEq(e.value!.length, 1000);
  t.done();
});

Deno.test("bundle size caps: 21 frames, 50 KB+ frame, non-JPEG, junk all rejected; long text trimmed", async () => {
  const t = await setup(MENV);
  await t.api("/api/defer", "POST", { job: JOB });
  const c = await (await t.mac("/api/mac/next")).json();
  assertEq((await t.mac("/api/mac/bundle", "POST", bundleBody(50, c.lease, 21))).status, 400);
  assertEq((await t.mac("/api/mac/bundle", "POST", bundleBody(50, c.lease, 0))).status, 400);
  const big = bundleBody(50, c.lease, 1);
  big.frames[0].jpeg_b64 = jpeg(50_001);
  assertEq((await t.mac("/api/mac/bundle", "POST", big)).status, 413);
  const png = bundleBody(50, c.lease, 1);
  png.frames[0].jpeg_b64 = btoa("\x89PNG....");
  assertEq((await t.mac("/api/mac/bundle", "POST", png)).status, 400);
  const junk = bundleBody(50, c.lease, 1);
  junk.frames[0].jpeg_b64 = "!!!not base64";
  assertEq((await t.mac("/api/mac/bundle", "POST", junk)).status, 400);
  assertEq((await t.mac("/api/mac/bundle", "POST", "{not json")).status, 400);
  assertEq(fires(t).length, 0);
  const edge = bundleBody(50, c.lease, 20, { transcript: "x".repeat(100_000) });
  edge.frames[0].jpeg_b64 = jpeg(50_000);
  assertEq((await t.mac("/api/mac/bundle", "POST", edge)).status, 200);
  const id = JSON.parse(fires(t)[0].body.text).bundle_id;
  const meta = (await t.kv.get<{ transcript: string }>(["mac", "bundle", id, "meta"])).value!;
  assertEq(meta.transcript.length, 24_000);
  t.done();
});

Deno.test("bundle at the rate cap waits and fires on a later poll", async () => {
  const t = await setup({ ...MENV, MAX_RUNS_PER_HOUR: "1" });
  await t.hook(msg(1, 42)); // uses the only slot
  await t.api("/api/defer", "POST", { job: JOB });
  const c = await (await t.mac("/api/mac/next")).json();
  assertEq(
    (await (await t.mac("/api/mac/bundle", "POST", bundleBody(50, c.lease))).json()).fired,
    false,
  );
  assertEq(fires(t).length, 1);
  t.clock.t += 3600000; // next hour
  await t.mac("/api/mac/next");
  assertEq(fires(t).length, 2);
  assertEq(JSON.parse(fires(t)[1].body.text).update_id, 50);
  t.done();
});

Deno.test("fail: real cause reaches the user, job is removed", async () => {
  const t = await setup(MENV);
  await t.api("/api/defer", "POST", { job: JOB });
  const c = await (await t.mac("/api/mac/next")).json();
  const r = await t.mac("/api/mac/fail", "POST", {
    update_id: 50,
    lease: c.lease,
    reason: "rate_limited",
  });
  assertEq(r.status, 200);
  const m = t.sent().at(-1)!;
  if (!m.includes("https://youtu.be/abc") || !m.includes("rate limiting")) throw new Error(m);
  assertEq((await t.mac("/api/mac/next")).status, 204);
  // wrong lease is refused
  await t.api("/api/defer", "POST", { job: { ...JOB, update_id: 51 } });
  await t.mac("/api/mac/next");
  assertEq(
    (await t.mac("/api/mac/fail", "POST", { update_id: 51, lease: "guess", reason: "blocked" }))
      .status,
    409,
  );
  t.done();
});

Deno.test("expiry: after 24 h the user is told, naming the link, on a webhook or a poll or sweep()", async () => {
  for (const how of ["poll", "webhook", "sweep"]) {
    const t = await setup(MENV);
    await t.api("/api/defer", "POST", { job: JOB });
    const before = t.sent().length;
    t.clock.t += 23 * 3600 * 1000;
    await t.handler.sweep();
    assertEq(t.sent().length, before); // not yet
    t.clock.t += 2 * 3600 * 1000;
    if (how === "poll") await t.mac("/api/mac/next");
    else if (how === "webhook") await t.hook(msg(99, 999));
    else await t.handler.sweep();
    const m = t.sent().at(-1)!;
    if (!m.includes("https://youtu.be/abc") || !m.includes("Send the link again")) {
      throw new Error(how + m);
    }
    await t.handler.sweep();
    assertEq(t.sent().length, before + 1); // told once, then gone
    assertEq((await t.mac("/api/mac/next")).status, 204);
    t.done();
  }
});
