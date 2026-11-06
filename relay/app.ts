// Omnividens relay: Telegram webhook <-> cloud routine. Never log tokens, message text or file bytes.

export interface Deps {
  env: (name: string) => string | undefined;
  kv: Deno.Kv;
  fetch: typeof fetch;
  now: () => number;
  randomId: () => string;
}

const ADOPT_RE =
  /^git clone -- https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]*\/(?!\.{1,2}$)[A-Za-z0-9._-]+$/;
const FILE_ID_RE = /^[\w-]{1,256}$/;
// A stalled service must not hold the webhook open. Each timeout takes that call's normal failure path.
const TG_TIMEOUT_MS = 15_000;
const FIRE_TIMEOUT_MS = 30_000;
const MAX_FILE = 20 * 1024 * 1024;
const MAX_PROFILE = 64 * 1024;
const MAX_PROJECT_LINES = 20;
const MAX_PROJECT_LINE = 200;
const MAX_GOAL_LINES = 5;
const MAX_GOAL_LINE = 300;
const DAY = 24 * 3600 * 1000;
const MAC_ONLINE_MS = 3 * 60 * 1000;
const LEASE_MS = 15 * 60 * 1000;
const MAC_MAX_ATTEMPTS = 2;
const MAC_MAX_PENDING = 5;
const MAC_JOB_TTL = DAY; // after this the user is told and the job dropped
const MAC_KV_TTL = 7 * DAY; // KV entry outlives the logical expiry so a sweep can still tell the user
const MAX_FRAMES = 20;
const MAX_FRAME_BYTES = 50_000; // a Deno KV value maxes out at 64 KiB
const MAX_TRANSCRIPT = 24_000; // chars; meta stays under 64 KiB even at 2 bytes a char
const MAX_CAPTION = 3_000;
const MAX_BUNDLE_BODY = 1_500_000;
const BUNDLE_ID_RE = /^[\w-]{1,64}$/;
const FAIL_REASONS = ["blocked", "rate_limited", "bad_link", "network", "too_long", "other"];
const URL_RE = /https?:\/\/[^\s<>"')\]]+/i; // same pattern as reelay/cloud/job.py, which reads the link
const NO_LINK_REPLY =
  "Send me a video or a link to a Reel/TikTok/Short. /help lists what I can do.";
const HELP_REPLY =
  `Send me a video (up to 20 MB) or a link to a Reel, TikTok or Short. I fact-check it and report on any GitHub tools it mentions. Each one takes a few minutes and uses your Claude usage.

/report owner/repo: report on one GitHub repo. This also uses your Claude usage.
/goals: show your goals. Put lines after it to replace them, or "clear" to delete them.
/projects: show your projects. Put lines after it to replace them, or "clear" to delete them.
/help: show this list.

Adopt never installs anything. It only gives you a git clone line to run yourself.`;
const NOT_VIDEO_REPLY =
  "That file isn't a video. Send me a video or a link to a Reel/TikTok/Short.";
const PHOTO_REPLY = "I can't read photos yet. Send me a video or a link to a Reel/TikTok/Short.";
const TOO_BIG_REPLY =
  "That video is over the 20 MB limit for Telegram bots, so I can't open it. Send a link to it, or a shorter clip.";
const REQUIRED = [
  "TELEGRAM_BOT_TOKEN",
  "OWNER_TELEGRAM_ID",
  "WEBHOOK_SECRET",
  "RELAY_SECRET",
  "ROUTINE_FIRE_URL",
  "ROUTINE_TOKEN",
];

// deno-lint-ignore no-explicit-any
type Json = any;

// Telegram may send no type, or a generic one, for a video sent as a file: let ffmpeg decide those.
function isVideoFile(f: Json): boolean {
  const mime = String(f?.mime_type ?? "").toLowerCase();
  return !mime || mime === "application/octet-stream" || mime.startsWith("video/");
}

async function safeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const x = new Uint8Array(ha), y = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
const text = (s: string, status: number) => new Response(s, { status });

export type Handler = ((req: Request) => Promise<Response>) & { sweep: () => Promise<void> };

export function makeHandler(deps: Deps): Handler {
  const { env, kv } = deps;
  const token = () => env("TELEGRAM_BOT_TOKEN") ?? "";

  async function tg(method: string, body: Json): Promise<Json> {
    try {
      const r = await deps.fetch(`https://api.telegram.org/bot${token()}/${method}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(TG_TIMEOUT_MS),
      });
      return await r.json();
    } catch {
      return { ok: false };
    }
  }
  const say = (chat_id: number, t: string, extra: Json = {}) =>
    tg("sendMessage", { chat_id, text: t, ...extra });

  // This page is public, so opening it must never move the bot to whatever origin asked: a preview
  // deployment or an arbitrary Host. The webhook is set only when this page is served from PUBLIC_URL,
  // so a typo or a preview can't move the bot or change its secret. Anywhere else the page just reads
  // Telegram's current state, so an existing webhook stays as it is.
  function publicUrl(): string | null {
    try {
      const u = new URL((env("PUBLIC_URL") ?? "").trim());
      if (u.protocol !== "https:" || u.search || u.hash) return null;
      return u.origin;
    } catch {
      return null;
    }
  }

  async function status(origin: string): Promise<Response> {
    const missing = REQUIRED.filter((n) => !env(n));
    const base = publicUrl();
    const badUrl = !base && (env("PUBLIC_URL") ?? "").trim() !== "";
    let hook: "here" | "elsewhere" | "none" | "refused" = "refused";
    if (env("TELEGRAM_BOT_TOKEN") && env("WEBHOOK_SECRET")) {
      if (base === origin) {
        const r = await tg("setWebhook", {
          url: `${base}/telegram`,
          secret_token: env("WEBHOOK_SECRET"),
          allowed_updates: ["message", "callback_query"],
        });
        if (r?.ok === true) hook = "here";
      } else {
        const r = await tg("getWebhookInfo", {});
        if (r?.ok === true) {
          const url = r.result?.url;
          hook = !url ? "none" : url === `${origin}/telegram` ? "here" : "elsewhere";
        }
      }
    }
    const ok = missing.length === 0 && !badUrl && hook === "here";
    const body = ok
      ? "<h1>Connected ✓</h1><p>Send your bot a message on Telegram.</p>"
      : `<h1>Not connected yet</h1>${
        missing.length
          ? `<p>Missing environment variables: ${missing.join(", ")}</p>`
          : badUrl
          ? "<p>PUBLIC_URL must be this app's https address, for example https://my-app.deno.net</p>"
          : base && base !== origin
          ? "<p>PUBLIC_URL is a different address from this page, so the webhook was not changed. Open the page at that address, or correct PUBLIC_URL.</p>"
          : hook === "none"
          ? "<p>The Telegram webhook isn't set. Add PUBLIC_URL (this app's production https address) and reload this page once.</p>"
          : hook === "elsewhere"
          ? "<p>The Telegram webhook points at a different address, which is expected on a preview. If this is your production address, add PUBLIC_URL and reload this page once.</p>"
          : "<p>Telegram did not accept the request. Check TELEGRAM_BOT_TOKEN.</p>"
      }`;
    return new Response(
      `<!doctype html><meta charset="utf-8"><title>Omnividens relay</title>${body}`,
      { headers: { "content-type": "text/html; charset=utf-8" } },
    );
  }

  // ---- Telegram webhook ----
  async function telegram(req: Request): Promise<Response> {
    const secret = env("WEBHOOK_SECRET");
    const got = req.headers.get("x-telegram-bot-api-secret-token") ?? "";
    if (!secret || !(await safeEqual(got, secret))) return text("unauthorized", 401);
    let u: Json;
    try {
      u = await req.json();
    } catch {
      return text("ok", 200);
    }
    if (typeof u?.update_id !== "number") return text("ok", 200);
    await sweep().catch(() => {}); // expired Mac jobs get their message even without cron
    const fresh = await kv.atomic().check({ key: ["upd", u.update_id], versionstamp: null })
      .set(["upd", u.update_id], 1, { expireIn: DAY }).commit();
    if (!fresh.ok) return text("ok", 200);

    const owner = env("OWNER_TELEGRAM_ID");
    const msg = u.message, cb = u.callback_query;
    const fromId = String((msg ?? cb)?.from?.id ?? "");
    if (!fromId) return text("ok", 200);
    if (!owner) {
      if (
        msg && typeof msg.text === "string" && msg.text.trim().split(/\s/)[0].startsWith("/start")
      ) {
        await say(msg.chat.id, `Your Telegram ID is ${fromId}. Put it in OWNER_TELEGRAM_ID.`);
      }
      return text("ok", 200);
    }
    if (fromId !== owner.trim()) return text("ok", 200);

    if (cb) await callback(cb);
    else if (msg) await ownerMessage(msg, u.update_id, new URL(req.url).origin);
    return text("ok", 200);
  }

  async function callback(cb: Json) {
    const chat = cb.message?.chat?.id ?? cb.from?.id; // message is inaccessible after 48 h
    const m = /^(adopt|skip):([\w-]{1,64})$/.exec(String(cb.data ?? ""));
    await tg("answerCallbackQuery", { callback_query_id: cb.id });
    if (!m || typeof chat !== "number") return;
    if (m[1] === "skip") return void await say(chat, "Skipped.");
    const stored = (await kv.get<string | { line: string; note?: string }>(["adopt", m[2]])).value;
    const line = typeof stored === "string" ? stored : stored?.line;
    const note = typeof stored === "object" && stored?.note ? `${stored.note}\n\n` : "";
    await say(chat, line ? note + line : "That suggestion expired.");
  }

  // Owner's written project list: handled here, before any routine run, so it costs no routine call.
  async function projectsCommand(chat: number, arg: string) {
    const cur = (await kv.get<string[]>(["projects"])).value ?? [];
    if (!arg) {
      return void await say(
        chat,
        cur.length
          ? `Your projects (${cur.length}):\n${cur.join("\n")}`
          : "No project list yet. Send /projects followed by lines like: Name: one line about it.",
      );
    }
    if (arg.toLowerCase() === "clear") {
      await kv.delete(["projects"]);
      return void await say(chat, "Project list cleared.");
    }
    const lines = arg.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length > MAX_PROJECT_LINES || lines.some((l) => l.length > MAX_PROJECT_LINE)) {
      return void await say(
        chat,
        `Not saved: at most ${MAX_PROJECT_LINES} lines, ${MAX_PROJECT_LINE} characters each.`,
      );
    }
    await kv.set(["projects"], lines);
    await say(chat, `Saved ${lines.length} project${lines.length === 1 ? "" : "s"}.`);
  }

  // Per-hour cap on routine fires, shared by Telegram messages and Mac bundles.
  async function takeRateSlot(): Promise<boolean> {
    const hour = Math.floor(deps.now() / 3600000);
    const key = ["rate", hour];
    const max = Number(env("MAX_RUNS_PER_HOUR")) || 20;
    for (;;) { // check-and-set so two messages at once can't both slip under the cap
      const cur = await kv.get<number>(key);
      const n = cur.value ?? 0;
      if (n >= max) return false;
      const res = await kv.atomic().check(cur).set(key, n + 1, { expireIn: 2 * 3600 * 1000 })
        .commit();
      if (res.ok) return true;
    }
  }

  // 0 = fired, >0 = HTTP status, -1 = unreachable.
  async function fireRoutine(job: Json): Promise<number> {
    let code = 0;
    try {
      const r = await deps.fetch(env("ROUTINE_FIRE_URL")!, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env("ROUTINE_TOKEN")}`,
          "anthropic-beta": "experimental-cc-routine-2026-04-01",
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({ text: JSON.stringify(job) }),
        signal: AbortSignal.timeout(FIRE_TIMEOUT_MS),
      });
      if (!r.ok) code = r.status;
      await r.body?.cancel();
    } catch {
      code = -1;
    }
    return code;
  }

  // Owner's goals: same pattern as /projects, own key, so a profile write by the routine can't erase them.
  async function goalsCommand(chat: number, arg: string) {
    const cur = (await kv.get<string[]>(["goals"])).value ?? [];
    if (!arg) {
      return void await say(
        chat,
        cur.length
          ? `Your goals (${cur.length}):\n${cur.join("\n")}`
          : "No goals yet. Send /goals followed by up to 5 lines, one goal per line.",
      );
    }
    if (arg.toLowerCase() === "clear") {
      await kv.delete(["goals"]);
      return void await say(chat, "Goals cleared.");
    }
    const lines = arg.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length > MAX_GOAL_LINES || lines.some((l) => l.length > MAX_GOAL_LINE)) {
      return void await say(
        chat,
        `Not saved: at most ${MAX_GOAL_LINES} lines, ${MAX_GOAL_LINE} characters each.`,
      );
    }
    await kv.set(["goals"], lines);
    await say(chat, `Saved ${lines.length} goal${lines.length === 1 ? "" : "s"}.`);
  }

  async function ownerMessage(msg: Json, updateId: number, origin: string) {
    const chat: number = msg.chat.id;
    const files = [msg.video, msg.video_note, msg.document, msg.animation].filter((f) =>
      f?.file_id
    );
    const text_: string = String(msg.text ?? msg.caption ?? "");
    if (!text_ && !files.length) {
      if (Array.isArray(msg.photo) && msg.photo.length) await say(chat, PHOTO_REPLY);
      return;
    }
    if (!files.length && /^\/(help|start)(@\w+)?(\s|$)/.test(text_.trim())) {
      return void await say(chat, HELP_REPLY);
    }
    if (!files.length && /^\/goals(@\w+)?(\s|$)/.test(text_.trim())) {
      return void await goalsCommand(chat, text_.trim().replace(/^\/goals(@\w+)?/, "").trim());
    }
    if (!files.length && /^\/projects(@\w+)?(\s|$)/.test(text_.trim())) {
      return void await projectsCommand(
        chat,
        text_.trim().replace(/^\/projects(@\w+)?/, "").trim(),
      );
    }

    const isReport = !files.length && /^\/report(@\w+)?(\s|$)/.test(text_.trim());
    if (isReport) { // one repo, one routine run
      const t = text_.trim();
      // prepare.py refuses .git names too, so don't spend a run finding that out.
      if (
        !/^\/report(@\w+)?\s+[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9][\w.-]*$/.test(t) ||
        /\.git$/i.test(t)
      ) {
        return void await say(
          chat,
          "Send /report owner/repo, for example /report anthropics/skills",
        );
      }
    }

    // A routine run costs the owner's usage: start one only if it has something it can work on.
    const videos = files.filter(isVideoFile);
    const usable = videos.filter((v) => (v.file_size ?? 0) <= MAX_FILE);
    if (!isReport && !usable.length && !URL_RE.test(text_)) {
      return void await say(
        chat,
        videos.length ? TOO_BIG_REPLY : files.length ? NOT_VIDEO_REPLY : NO_LINK_REPLY,
      );
    }

    if (!(await takeRateSlot())) return void await say(chat, "Busy, try again later");

    await say(chat, "Got it. Takes a few minutes.");
    const f = usable.sort((a, b) => (b.file_size ?? 0) - (a.file_size ?? 0))[0];
    const job = {
      v: 1,
      update_id: updateId,
      chat_id: chat,
      message_id: msg.message_id,
      text: text_.slice(0, 4000),
      file: f
        ? { file_id: f.file_id, file_size: f.file_size ?? 0, mime_type: f.mime_type ?? "" }
        : null,
      relay_url: origin,
    };
    const code = await fireRoutine(job);
    if (code) {
      await say(
        chat,
        code > 0
          ? `Could not start the analysis (routine returned HTTP ${code}).`
          : "Could not start the analysis (routine unreachable).",
      );
    }
  }

  // ---- Mac fallback queue ----
  // KV: ["mac","job",update_id] -> MacJob, ["mac","seen"] -> ms, ["mac","bundle",id,"meta"|"frame",i].
  interface MacJob {
    job: Json;
    created: number;
    attempts: number;
    lease_until: number;
    lease: string;
    state: "queued" | "ready";
    bundle_id?: string;
  }
  const jobKey = (u: number) => ["mac", "job", u];

  function linkOf(job: Json): string {
    return URL_RE.exec(String(job?.text ?? ""))?.[0].replace(/[.,;:!?]+$/, "").slice(0, 300) ??
      "your video";
  }

  function seenText(ms: number | null): string {
    return ms ? new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "never";
  }

  async function macSeen(): Promise<number | null> {
    return (await kv.get<number>(["mac", "seen"])).value;
  }

  // Fire the routine for a job whose bundle is stored. The row is claimed first (check-and-delete),
  // so a webhook or poll sweeping at the same moment cannot fire it a second time.
  async function fireReady(entry: Deno.KvEntry<MacJob>): Promise<boolean> {
    const rec = entry.value;
    const claim = await kv.atomic().check(entry).delete(entry.key).commit();
    if (!claim.ok) return true; // someone else has it
    if (!(await takeRateSlot())) {
      // hourly cap is full: put it back (unless it was re-created meanwhile) and try on a later sweep
      await kv.atomic().check({ key: entry.key, versionstamp: null })
        .set(entry.key, rec, { expireIn: MAC_KV_TTL }).commit();
      return false;
    }
    const code = await fireRoutine({ ...rec.job, bundle_id: rec.bundle_id });
    if (code) {
      await say(
        rec.job.chat_id,
        `Your Mac fetched ${
          linkOf(rec.job)
        }, but I could not start the analysis. Send the link again.`,
      );
    }
    return true;
  }

  // Expiry and lease give-up. Runs on every webhook, every worker poll and (if the platform has it) cron.
  async function sweep(): Promise<void> {
    const now = deps.now();
    for await (const e of kv.list<MacJob>({ prefix: ["mac", "job"] })) {
      const rec = e.value;
      const drop = () => kv.atomic().check(e).delete(e.key).commit(); // only one sweep tells the user
      if (now - rec.created >= MAC_JOB_TTL) {
        if (!(await drop()).ok) continue;
        await say(
          rec.job.chat_id,
          rec.state === "ready"
            ? `I fetched ${linkOf(rec.job)} but my hourly run limit stayed full for 24 hours. ` +
              "Send the link again and I'll retry."
            : `I couldn't run ${linkOf(rec.job)} because your Mac wasn't online for 24 hours. ` +
              "Send the link again and I'll retry.",
        );
      } else if (rec.state === "ready") {
        await fireReady(e);
      } else if (rec.lease_until && rec.lease_until <= now && rec.attempts >= MAC_MAX_ATTEMPTS) {
        if (!(await drop()).ok) continue;
        await say(
          rec.job.chat_id,
          `Your Mac started ${linkOf(rec.job)} twice but never finished it. ` +
            "Send the link again when it's awake.",
        );
      }
    }
  }

  // RELAY_SECRET: the routine hands a blocked link job to the Mac queue.
  async function defer(req: Request, origin: string): Promise<Response> {
    if (req.method !== "POST") return text("method not allowed", 405);
    const b = await req.json().catch(() => null);
    const j = b?.job;
    const owner = (env("OWNER_TELEGRAM_ID") ?? "").trim();
    if (
      j?.v !== 1 || typeof j.update_id !== "number" || typeof j.chat_id !== "number" ||
      typeof j.text !== "string" || j.bundle_id !== undefined || j.file
    ) return json({ error: "bad request" }, 400);
    if (!owner || String(j.chat_id) !== owner) return json({ error: "not the owner" }, 403);
    if (!env("MAC_SECRET")) return json({ queued: false, reason: "no_mac" });
    // The job must come from a real owner message: the webhook wrote this row.
    if (!(await kv.get(["upd", j.update_id])).value) return json({ error: "unknown update" }, 403);
    let pending = 0;
    for await (const _ of kv.list({ prefix: ["mac", "job"] })) pending++;
    if (pending >= MAC_MAX_PENDING && !(await kv.get(jobKey(j.update_id))).value) {
      await say(
        j.chat_id,
        `Your Mac already has ${MAC_MAX_PENDING} videos waiting, so I can't hold ${
          linkOf(j)
        }. Send it again once they're done.`,
      );
      return json({ queued: false, reason: "queue_full", told: true });
    }
    const clean = {
      v: 1,
      update_id: j.update_id,
      chat_id: j.chat_id,
      message_id: typeof j.message_id === "number" ? j.message_id : 0,
      text: j.text.slice(0, 4000),
      file: null,
      relay_url: origin, // our own origin, never the caller's
    };
    const rec: MacJob = {
      job: clean,
      created: deps.now(),
      attempts: 0,
      lease_until: 0,
      lease: "",
      state: "queued",
    };
    const put = await kv.atomic().check({ key: jobKey(clean.update_id), versionstamp: null })
      .set(jobKey(clean.update_id), rec, { expireIn: MAC_KV_TTL }).commit();
    if (!put.ok) return json({ queued: true, duplicate: true });
    const seen = await macSeen();
    const online = seen !== null && deps.now() - seen < MAC_ONLINE_MS;
    await say(
      clean.chat_id,
      online
        ? "YouTube blocks downloads from the cloud, so your Mac is fetching this one. Your report follows in a few minutes."
        : `YouTube blocks downloads from the cloud, and your Mac isn't online (last seen ${
          seenText(seen)
        }). I'll keep this for 24 hours and run it when your Mac is awake.`,
    );
    return json({ queued: true, online });
  }

  // RELAY_SECRET: the routine reads the Mac's upload.
  async function bundleGet(id: string): Promise<Response> {
    if (!BUNDLE_ID_RE.test(id)) return json({ error: "bad id" }, 400);
    const meta = (await kv.get<Json>(["mac", "bundle", id, "meta"])).value;
    if (!meta) return json({ error: "not found" }, 404);
    const frames: Json[] = [];
    for (let i = 0; i < meta.times.length; i++) {
      const v = (await kv.get<Uint8Array>(["mac", "bundle", id, "frame", i])).value;
      if (!v) return json({ error: "incomplete" }, 404);
      frames.push({ time_s: meta.times[i], jpeg_b64: encodeBase64(v) });
    }
    return json({
      meta: {
        duration_s: meta.duration_s,
        transcript: meta.transcript,
        caption: meta.caption,
      },
      frames,
    });
  }

  function encodeBase64(b: Uint8Array): string {
    let s = "";
    for (let i = 0; i < b.length; i += 0x8000) {
      s += String.fromCharCode(...b.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  function decodeBase64(s: string): Uint8Array | null {
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s) || s.length % 4) return null;
    try {
      return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
    } catch {
      return null;
    }
  }

  // MAC_SECRET: the worker routes. This secret opens nothing else, and RELAY_SECRET opens none of these.
  async function macApi(req: Request, path: string): Promise<Response> {
    const secret = env("MAC_SECRET");
    const auth = req.headers.get("authorization") ?? "";
    if (!secret || !(await safeEqual(auth, `Bearer ${secret}`))) return text("unauthorized", 401);
    const now = deps.now();
    await kv.set(["mac", "seen"], now, { expireIn: 30 * DAY });
    await sweep();

    if (path === "/api/mac/next") {
      if (req.method !== "GET") return text("method not allowed", 405);
      for (;;) { // oldest first; claim with a check-and-set so a double poll can't claim twice
        let pick: Deno.KvEntry<MacJob> | null = null;
        for await (const e of kv.list<MacJob>({ prefix: ["mac", "job"] })) {
          const r = e.value;
          if (r.state === "queued" && r.lease_until <= now && r.attempts < MAC_MAX_ATTEMPTS) {
            pick = e;
            break;
          }
        }
        if (!pick) return new Response(null, { status: 204 });
        const rec = {
          ...pick.value,
          attempts: pick.value.attempts + 1,
          lease_until: now + LEASE_MS,
          lease: deps.randomId(),
        };
        const res = await kv.atomic().check(pick).set(pick.key, rec, { expireIn: MAC_KV_TTL })
          .commit();
        if (res.ok) {
          return json({
            update_id: rec.job.update_id,
            lease: rec.lease,
            attempt: rec.attempts,
            job: rec.job,
          });
        }
      }
    }

    if (req.method !== "POST") return text("method not allowed", 405);
    if (path !== "/api/mac/bundle" && path !== "/api/mac/fail") return text("not found", 404);
    const raw = new Uint8Array(await req.arrayBuffer());
    if (raw.length > MAX_BUNDLE_BODY) return json({ error: "too large" }, 413);
    let b: Json;
    try {
      b = JSON.parse(new TextDecoder().decode(raw));
    } catch {
      return json({ error: "invalid JSON" }, 400);
    }
    if (typeof b?.update_id !== "number" || typeof b?.lease !== "string") {
      return json({ error: "bad request" }, 400);
    }
    const cur = await kv.get<MacJob>(jobKey(b.update_id));
    const rec = cur.value;
    if (!rec || !rec.lease || !(await safeEqual(rec.lease, b.lease)) || rec.state !== "queued") {
      return json({ error: "no such lease" }, 409);
    }

    if (path === "/api/mac/fail") {
      const reason = FAIL_REASONS.includes(b.reason) ? b.reason : "other";
      const host = typeof b.host === "string"
        ? b.host.replace(/[^A-Za-z0-9.-]/g, "").slice(0, 60)
        : "";
      const link = linkOf(rec.job);
      const why: Record<string, string> = {
        blocked: "YouTube's bot check blocked your Mac too",
        rate_limited: "the site is rate limiting downloads from your Mac right now",
        bad_link: "the link is private, removed or not supported",
        network: `your Mac couldn't reach ${host || "that site"}`,
        too_long: "the video is over the 10 minute limit",
        other: "your Mac couldn't download it",
      };
      await kv.delete(cur.key);
      await say(
        rec.job.chat_id,
        `I couldn't get ${link}: ${why[reason]}. Send the video file instead, or try again later.`,
      );
      return json({ ok: true });
    }

    // bundle
    const m = b.meta, fr = b.frames;
    if (!m || typeof m !== "object" || !Array.isArray(fr) || !fr.length || fr.length > MAX_FRAMES) {
      return json({ error: "bad bundle" }, 400);
    }
    const bytes: Uint8Array[] = [], times: number[] = [];
    for (const f of fr) {
      const d = typeof f?.jpeg_b64 === "string" && f.jpeg_b64.length <= MAX_FRAME_BYTES * 2
        ? decodeBase64(f.jpeg_b64)
        : null;
      if (
        !d || typeof f.time_s !== "number" || !Number.isFinite(f.time_s) || f.time_s < 0 ||
        f.time_s >= 3600 || d[0] !== 0xff || d[1] !== 0xd8 || d[2] !== 0xff
      ) return json({ error: "bad frame" }, 400);
      if (d.length > MAX_FRAME_BYTES) return json({ error: "frame too large" }, 413);
      bytes.push(d);
      times.push(f.time_s);
    }
    const id = deps.randomId();
    const meta = {
      times,
      duration_s: typeof m.duration_s === "number" && Number.isFinite(m.duration_s)
        ? m.duration_s
        : 0,
      transcript: typeof m.transcript === "string" ? m.transcript.slice(0, MAX_TRANSCRIPT) : "",
      caption: typeof m.caption === "string" ? m.caption.slice(0, MAX_CAPTION) : "",
    };
    for (let i = 0; i < bytes.length; i++) {
      await kv.set(["mac", "bundle", id, "frame", i], bytes[i], { expireIn: DAY });
    }
    await kv.set(["mac", "bundle", id, "meta"], meta, { expireIn: DAY });
    const ready: MacJob = { ...rec, state: "ready", bundle_id: id };
    const put = await kv.set(cur.key, ready, { expireIn: MAC_KV_TTL });
    const fired = await fireReady({ key: cur.key, value: ready, versionstamp: put.versionstamp });
    return json({ ok: true, fired });
  }

  // ---- Routine API ----
  async function api(req: Request, path: string): Promise<Response> {
    const secret = env("RELAY_SECRET");
    const auth = req.headers.get("authorization") ?? "";
    if (!secret || !(await safeEqual(auth, `Bearer ${secret}`))) return text("unauthorized", 401);

    if (path === "/api/defer") return await defer(req, new URL(req.url).origin);
    if (path.startsWith("/api/bundle/")) {
      if (req.method !== "GET") return text("method not allowed", 405);
      return await bundleGet(path.slice("/api/bundle/".length));
    }

    if (path === "/api/send") {
      if (req.method !== "POST") return text("method not allowed", 405);
      const b = await req.json().catch(() => null);
      const line = b?.adopt_line ?? null;
      if (
        typeof b?.chat_id !== "number" || typeof b?.text !== "string" || !b.text ||
        (line !== null && (typeof line !== "string" || !ADOPT_RE.test(line)))
      ) return json({ error: "bad request" }, 400);
      if (b.report === true) { // daily report budget; one report per send
        const day = Math.floor(deps.now() / DAY);
        const max = Number(env("MAX_REPORTS_PER_DAY")) || 30;
        let booked = false;
        for (let tries = 0; tries < 10 && !booked; tries++) {
          const cur = await kv.get<number>(["reports", day]);
          const n = cur.value ?? 0;
          if (n >= max) return json({ error: "daily report limit" }, 429);
          const res = await kv.atomic().check(cur).set(["reports", day], n + 1, {
            expireIn: 2 * DAY,
          })
            .commit();
          booked = res.ok;
        }
        if (!booked) return json({ error: "busy, try again" }, 503);
      }
      const parts: string[] = [];
      for (let i = 0; i < b.text.length; i += 4096) parts.push(b.text.slice(i, i + 4096));
      let id: string | null = null;
      if (line) {
        id = deps.randomId();
        const note = typeof b.adopt_note === "string"
          // code-written plain text only: no control characters, capped
          ? b.adopt_note.replace(/[\x00-\x09\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, " ").slice(
            0,
            600,
          )
          : "";
        await kv.set(["adopt", id], note ? { line, note } : line, { expireIn: 7 * DAY });
      }
      for (let i = 0; i < parts.length; i++) {
        const last = i === parts.length - 1;
        const extra = last && id
          ? {
            reply_markup: {
              inline_keyboard: [[
                { text: "Adopt", callback_data: `adopt:${id}` },
                { text: "Skip", callback_data: `skip:${id}` },
              ]],
            },
          }
          : {};
        const r = await say(b.chat_id, parts[i], extra);
        if (!r?.ok) return json({ error: "telegram send failed" }, 502);
      }
      return json({ ok: true, parts: parts.length });
    }

    if (path.startsWith("/api/file/")) {
      if (req.method !== "GET") return text("method not allowed", 405);
      const fileId = path.slice("/api/file/".length);
      if (!FILE_ID_RE.test(fileId)) return json({ error: "bad file_id" }, 400);
      const r = await tg("getFile", { file_id: fileId });
      const info = r?.result;
      if (!r?.ok || !info?.file_path) return json({ error: "file not found" }, 404);
      if ((info.file_size ?? 0) > MAX_FILE) return json({ error: "file too large" }, 413);
      const f = await deps.fetch(`https://api.telegram.org/file/bot${token()}/${info.file_path}`);
      if (!f.ok || !f.body) return json({ error: "download failed" }, 502);
      if (Number(f.headers.get("content-length") ?? 0) > MAX_FILE) {
        await f.body.cancel();
        return json({ error: "file too large" }, 413);
      }
      return new Response(f.body, { headers: { "content-type": "application/octet-stream" } });
    }

    if (path === "/api/profile") {
      if (req.method === "GET") {
        const prof = (await kv.get<Json>(["profile"])).value ?? {};
        const projects = (await kv.get<string[]>(["projects"])).value ?? [];
        const goals = (await kv.get<string[]>(["goals"])).value;
        // own keys always win over anything stored in the profile object
        return json({ ...prof, goals: goals ?? [], projects });
      }
      if (req.method === "PUT") {
        const raw = new Uint8Array(await req.arrayBuffer());
        if (raw.length > MAX_PROFILE) return json({ error: "profile too large" }, 413);
        let v: Json;
        try {
          v = JSON.parse(new TextDecoder().decode(raw));
        } catch {
          return json({ error: "invalid JSON" }, 400);
        }
        if (v === null || typeof v !== "object" || Array.isArray(v)) {
          return json({ error: "profile must be a JSON object" }, 400);
        }
        delete v.projects; // the project list lives under its own key and is only set by /projects
        delete v.goals; // likewise goals: only /goals sets them
        await kv.set(["profile"], v);
        return json({ ok: true });
      }
      return text("method not allowed", 405);
    }
    return text("not found", 404);
  }

  const handler = async (req: Request): Promise<Response> => {
    const url = new URL(req.url);
    const path = url.pathname;
    try {
      if (path === "/") {
        return req.method === "GET" ? await status(url.origin) : text("method not allowed", 405);
      }
      if (path === "/telegram") {
        if (req.method !== "POST") return text("method not allowed", 405);
        // 200 even on internal errors: the update is already deduped, so a retry would be dropped.
        return await telegram(req).catch(() => text("ok", 200));
      }
      if (path.startsWith("/api/mac/")) return await macApi(req, path);
      if (path.startsWith("/api/")) return await api(req, path);
      return text("not found", 404);
    } catch {
      return text("error", 500);
    }
  };
  return Object.assign(handler, { sweep });
}
