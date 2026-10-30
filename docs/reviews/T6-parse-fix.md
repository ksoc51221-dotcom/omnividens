# T6 parse-fix review (R-11, R-12) — branch `agent/t6-parse-fix` @ e9dee4f

Reviewer: T13 session (Sonnet 5.5). Suite from `../reelay-t6b`: 315 passed. No code edited. I ran extra probes by hand.

**Verdict: FIX (one design gap), no blockers. Fails closed.**

## extract_json (R-11): fine
- Stray braces before the real object work (`x {a} {"summary":"ok","claims":[]} y`), code fences work, braces inside strings work.
- Fails closed: no object, an empty summary, a non-string summary, a top-level list, and deep recursion all give None. With two objects it takes the first with a summary.
- NIT-1: an object with no summary at the top level whose *nested* value has one (`{"a":{"summary":"deep"}}`) is accepted as the result. Harmless today (fields are still validated downstream) but it is a looser match than "the answer object"; consider requiring the object to be the top-level hit.
- NIT-2: `raw_decode` at every `{` is quadratic on long brace-heavy output. Model output is bounded, so fine.

## strip_urls (R-12)
Kept (good): `install.sh`, `main.cc`, `next.ai`, `Node.js`. Removed (good): `http(s)://`, `www.`, emails, `bit.ly/abc`, `t.co/abc`, `goo.gl/x`, `youtu.be/abc`, `evil.tk/a`, `EVIL.COM/a`, `evil.sh/`, `evil.dev`, `evil.app`, `a.b.c.xyz`, `example.com`.

- **FIX-1: bare domains on dropped TLDs now reach free text.** Before this change `evil.ai`, `evil.sh`, `evil.cc`, `evil.to` (also `.me`-style names not in the list like `evil.ms`) were stripped; now they pass through (`'evil.ai' -> 'evil.ai'`). Path forms are still caught. That is the intended trade (file names vs. domains), but it breaks the stated goal that real links never reach free text, and `.ai`/`.sh` are common for phishing-style names. Options: (a) keep stripping and accept the file-name false positives, (b) strip only when the token is not a known file name (e.g. keep a short allow-list: `install.sh`, `main.cc`, `next.ai`, `Node.js`, and anything with a common code extension), the reverse of the current list. Orchestrator should pick; documenting in SECURITY.md is the minimum.
- NIT-3: IP-with-path (`1.2.3.4/x`) and `localhost:8080/a` are not stripped (they weren't before either, and `good_url` rejects private hosts for the structured fields; only free text is affected).
- NIT-4: defanged forms (`evil[.]com`, `evil . com`) pass through; same as before, ignore.

## Re-check @ ea1277f (T13 session)
**APPROVE.** Bare domains on all earlier TLDs are stripped again (`evil.ai`, `evil.sh`, `evil.cc`, `evil.to` -> `[link removed]`). The allow-list is an exact, case-insensitive whole-match, so it cannot be used to smuggle a link: `evil.com/install.sh`, `evil.com.install.sh`, `install.sh.evil.com`, `www.install.sh`, `a@install.sh`, `install.sh/` and `xinstall.sh` are all stripped. Suite from `../reelay-t6b`: 315 passed. Accepted trade-off: `socket.io`, `three.dev` and `next.ai` are real domains left readable in free text (no scheme, not clickable in Telegram), and `my-install.sh`-style names are over-stripped. NIT-1 (nested summary) still open, optional.
