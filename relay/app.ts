// Reelay relay: Telegram webhook <-> cloud routine. Never log tokens, message text or file bytes.

export interface Deps {
  env: (name: string) => string | undefined;
  kv: Deno.Kv;
  fetch: typeof fetch;
  now: () => number;
  randomId: () => string;
}

const ADOPT_RE = /^git clone -- https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/;
const FILE_ID_RE = /^[\w-]{1,256}$/;
const MAX_FILE = 20 * 1024 * 1024;
const MAX_PROFILE = 64 * 1024;
const DAY = 24 * 3600 * 1000;
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

export function makeHandler(deps: Deps): (req: Request) => Promise<Response> {
  const { env, kv } = deps;
  const token = () => env("TELEGRAM_BOT_TOKEN") ?? "";

  async function tg(method: string, body: Json): Promise<Json> {
    try {
      const r = await deps.fetch(`https://api.telegram.org/bot${token()}/${method}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      return await r.json();
    } catch {
      return { ok: false };
    }
  }
  const say = (chat_id: number, t: string, extra: Json = {}) =>
    tg("sendMessage", { chat_id, text: t, ...extra });

  async function status(origin: string): Promise<Response> {
    const missing = REQUIRED.filter((n) => !env(n));
    let hook = false;
    if (env("TELEGRAM_BOT_TOKEN") && env("WEBHOOK_SECRET")) {
      const r = await tg("setWebhook", {
        url: `${origin}/telegram`,
        secret_token: env("WEBHOOK_SECRET"),
        allowed_updates: ["message", "callback_query"],
      });
      hook = r?.ok === true;
    }
    const ok = missing.length === 0 && hook;
    const body = ok
      ? "<h1>Connected ✓</h1><p>Send your bot a message on Telegram.</p>"
      : `<h1>Not connected yet</h1>${
        missing.length
          ? `<p>Missing environment variables: ${missing.join(", ")}</p>`
          : "<p>Telegram did not accept the webhook. Check TELEGRAM_BOT_TOKEN.</p>"
      }`;
    return new Response(
      `<!doctype html><meta charset="utf-8"><title>Reelay relay</title>${body}`,
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
    const chat = cb.message?.chat?.id;
    const m = /^(adopt|skip):([\w-]{1,64})$/.exec(String(cb.data ?? ""));
    await tg("answerCallbackQuery", { callback_query_id: cb.id });
    if (!m || typeof chat !== "number") return;
    if (m[1] === "skip") return void await say(chat, "Skipped.");
    const line = (await kv.get<string>(["adopt", m[2]])).value;
    await say(chat, line ?? "That suggestion expired.");
  }

  async function ownerMessage(msg: Json, updateId: number, origin: string) {
    const chat: number = msg.chat.id;
    const files = [msg.video, msg.document, msg.animation].filter((f) => f?.file_id);
    const text_: string = String(msg.text ?? msg.caption ?? "");
    if (!text_ && !files.length) return;

    const hour = Math.floor(deps.now() / 3600000);
    const key = ["rate", hour];
    const n = (await kv.get<number>(key)).value ?? 0;
    const max = Number(env("MAX_RUNS_PER_HOUR")) || 20;
    if (n >= max) return void await say(chat, "Busy, try again later");
    await kv.set(key, n + 1, { expireIn: 2 * 3600 * 1000 });

    await say(chat, "Got it. Takes a few minutes.");
    const f = files.sort((a, b) => (b.file_size ?? 0) - (a.file_size ?? 0))[0];
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
    let code = 0;
    try {
      const r = await deps.fetch(env("ROUTINE_FIRE_URL")!, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env("ROUTINE_TOKEN")}`,
          "anthropic-beta": "experimental-cc-routine-2026-04-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({ text: JSON.stringify(job) }),
      });
      if (!r.ok) code = r.status;
      await r.body?.cancel();
    } catch {
      code = -1;
    }
    if (code) {
      await say(
        chat,
        code > 0
          ? `Could not start the analysis (routine returned HTTP ${code}).`
          : "Could not start the analysis (routine unreachable).",
      );
    }
  }

  // ---- Routine API ----
  async function api(req: Request, path: string): Promise<Response> {
    const secret = env("RELAY_SECRET");
    const auth = req.headers.get("authorization") ?? "";
    if (!secret || !(await safeEqual(auth, `Bearer ${secret}`))) return text("unauthorized", 401);

    if (path === "/api/send") {
      if (req.method !== "POST") return text("method not allowed", 405);
      const b = await req.json().catch(() => null);
      const line = b?.adopt_line ?? null;
      if (
        typeof b?.chat_id !== "number" || typeof b?.text !== "string" || !b.text ||
        (line !== null && (typeof line !== "string" || !ADOPT_RE.test(line)))
      ) return json({ error: "bad request" }, 400);
      const parts: string[] = [];
      for (let i = 0; i < b.text.length; i += 4096) parts.push(b.text.slice(i, i + 4096));
      let id: string | null = null;
      if (line) {
        id = deps.randomId();
        await kv.set(["adopt", id], line, { expireIn: 7 * DAY });
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
        return json((await kv.get<Json>(["profile"])).value ?? {});
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
        await kv.set(["profile"], v);
        return json({ ok: true });
      }
      return text("method not allowed", 405);
    }
    return text("not found", 404);
  }

  return async (req) => {
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
      if (path.startsWith("/api/")) return await api(req, path);
      return text("not found", 404);
    } catch {
      return text("error", 500);
    }
  };
}
