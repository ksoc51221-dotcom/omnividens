# T37 review — `agent/t37-report-name` @ d8763e1 (reviewer: T13 session, Sonnet 5.5)

`cd relay && deno task test` in `../reelay-t37`: 69 passed. Read the full `relay/app.ts` diff and the test titles. Not run live.

**Verdict: APPROVE. No BLOCKER, no FIX, four NITs.**

## Attacks checked
- **Hostile GitHub result.** The repo name is shown only if `full_name` matches `OWNER_REPO_RE` (ASCII, no `..`) and does not end in `.git`; anything else is treated as "no result". The description goes through `plainText`: control and bidi characters become spaces, `http(s)://` and `www.` links are removed, whitespace is collapsed, 200 characters max. `say` sends no `parse_mode`, so `<`, `*` or `_` in a description cannot format anything. Buttons are fixed text; `callback_data` is only `rpick:`/`rno:` plus the random id, never GitHub text. The user's query is limited by `NAME_RE` (letters, digits, space, `._+-`), so it cannot carry search qualifiers like `user:` or `stars:`, and is URL-encoded.
- **Fire only on Yes.** `reportByName` stores the pick and sends a message; the only `fireRoutine` call is in `reportPick` after `kind === "rpick"`, the claim and the rate slot. The routine text is rebuilt from the stored `pick.repo` (validated), not from callback data. It matches the `/report owner/repo` form `prepare` already accepts, `.git` refusal included.
- **Double tap.** The row is claimed with `check(entry).delete(...)`; the loser returns silently. Two taps are two different `update_id`s, so the update dedupe does not hide the race, and the claim does the job. Test exists.
- **Expired or forged ids.** Missing row or a row for another chat → "That suggestion expired." The id is a 128-bit random value inside a 64-character `[\w-]` regex, the row has a 15-minute TTL, and only the owner's `from.id` gets past the webhook gate (stranger test exists). `adopt:`/`skip:` read a different key space, so ids can't be crossed.
- **Rate slot.** Search and No never call `takeRateSlot`; Yes takes one after the claim. If the hour is full the row is restored (guarded by `versionstamp: null`) so a later tap works.
- **403, 429, timeout, bad JSON, empty result.** All return `null` → `SEARCH_FAILED` with the manual `owner/repo` hint; non-200 bodies are cancelled; `AbortSignal.timeout(10 s)`. The webhook is held up to 10 s at most, and Telegram retries of that update are deduped.
- **`/report owner/repo` unchanged.** Anything with a `/` still goes through the original regex plus the `\.git$` check (only the usage text changed); `/report` alone gives usage; files still disable it; the direct path still fires without a search (test exists).

## NIT
1. Telegram autolinks bare domains (`evil.com`), `@mentions` and `#tags` in plain text; `plainText` strips only `http(s)://`/`www.` links. Harmless (the owner has to tap, and the tappable "/report" command only sends the word `/report`, which returns usage), but stripping bare-domain-looking tokens, as T6's `strip_urls` now does, would make the claim "link-free" true.
2. `plainText` lists specific invisible characters. `\p{Cf}` (with the `u` flag) would also cover tag characters (U+E0000 range) and similar.
3. Race on the busy path: a second tap that lands between the claim and the restore gets "expired". Only a double tap in a full hour; accept.
4. Unauthenticated GitHub search is limited per source IP (about 10 requests a minute) and Deno Deploy shares IPs, so `SEARCH_FAILED` may be common at busy times. The fallback text covers it; adding a token would add a secret, so I would not.
