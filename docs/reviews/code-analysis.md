# Code analysis: slowdowns, hangs, duplicated logic

Read-only analysis of `main` at `c7c3316`, cloud path first, then local. Nothing was changed. Line numbers are from that commit.

**Labels:** LOW = safe tonight. AFTER = wait until after submission, because the fix could change a demo run or touches safety code.

**Evidence:** MEASURED means run on a scratch copy against fakes, with no real Telegram, routine or network. ESTIMATED shows the arithmetic. READ means from the code only.

**Assumption:** the demo uses the cloud path. `docs/DEMO.md` never mentions local mode.

## Bottom line
Three small fixes are safe tonight: 1, 3 and 6. Fix 1 is the only one worth a redeploy for its own sake. Everything else waits, including the biggest token saving (2), because it changes what the model writes.

## Findings, by payoff

### 1. No timeout on Telegram or routine calls: LOW
- **Where:** `relay/app.ts:96` (`tg()`), `relay/app.ts:259` (`fireRoutine`). `relay/app.ts:178` also waits for `sweep()`, which can call Telegram, before the dedupe step.
- **What happens:** if Telegram or the routine API stalls, the webhook waits with no limit. The update is marked as seen at `relay/app.ts:179` before the reply and the fire, so if the platform kills the stalled request, Telegram's retry is dropped as a duplicate and the video is silently lost (READ).
- **Cost:** MEASURED hang: with a routine call that never answers, the webhook was still waiting when the test stopped at 2 s. The real length is whatever the platform allows (not measured). No cost when the APIs are healthy.
- **Fix:** add `signal: AbortSignal.timeout(15_000)` in `tg()` and `AbortSignal.timeout(30_000)` in `fireRoutine`. Both already catch errors and report a failure.
- **Risk:** a healthy demo is unchanged. If `/fire` ever took over 30 s and still started, the owner would see a failure message for a run that goes ahead. The real `/fire` time hasn't been measured. Leave the file download at `relay/app.ts:741` alone: it streams a video.

### 2. The routine reads up to 40 KB per repo: AFTER
- **Where:** `routine/PROMPT.md:27`, one repo at a time per the rules at `routine/PROMPT.md:25-27`.
- **What happens:** each repo allows 20 KB of README, up to 20 KB of `SKILL.md` and a 200-line file list. All of it stays in context for every later turn.
- **Cost:** ESTIMATED worst case, using bytes / 4: 5 repos x about 26 KB = 130 KB, about 32k tokens. 9 skills at about 48 KB each is about 108k tokens. Real READMEs are often shorter; this has not been measured on a real run.
- **Fix:** cap the README at 6 KB, `SKILL.md` at 8 KB and the list at 80 lines. Clone all candidates in one Bash command.
- **Risk:** reports may lose detail, and `routine/PROMPT.md` takes effect on the next run with no deploy. Biggest token saving here, but it changes demo output.

### 3. `/report owner/repo.git` wastes a whole routine run: LOW
- **Where:** `relay/app.ts:328` accepts it; `reelay/cloud/prepare.py:128` then refuses it.
- **Cost:** MEASURED on the relay: 1 fire, 1 rate slot and a "Got it" reply. ESTIMATED downstream: one VM start plus about 2k tokens of prompt, ending in "Send /report owner/repo".
- **Fix:** refuse names ending in `.git` (any case) at `relay/app.ts:328`, using the same reply, and add a test. GitHub doesn't allow repo names ending in `.git`.
- **Risk:** none for a valid repo.

### 4. Cloud transcription has no deadline: AFTER
- **Where:** `reelay/cloud/speech.py:33`, called from `reelay/cloud/prepare.py:166`. The Mac worker has a cap (`reelay/cloud/mac_worker.py:156-158`); the cloud path doesn't.
- **What happens:** a video without captions runs the `base` model on CPU, and a fresh VM downloads the model first with no overall time limit. That time comes out of the prompt's 20-minute budget.
- **Cost:** ESTIMATED only: bounded by the 600 s video cap once the model is loaded. Not measured here. A stuck download was not reproduced.
- **Fix:** reuse the worker's thread-and-join wrapper, then fall back to the existing no-captions note.
- **Risk:** that wrapper has only been tested with a sleeping stand-in, never a real model, and caption-less demo videos would run through it.

### 5. The report step clones repos one at a time: AFTER
- **Where:** `reelay/cloud/report.py:181-190`.
- **Cost:** ESTIMATED: at about 2-4 s per small repo, 1-2 repos cost under 10 s. Worst case is 9 x 60 s = 9 minutes of the 20-minute budget.
- **Fix:** clone in a small thread pool, then run `apply_gate` in order. Do not reuse the model's clones: the gate must scan its own.
- **Risk:** this is the safety gate, and the normal saving is seconds. Not tonight.

### 6. The local bot parses links differently from the cloud: LOW
- **Where:** `reelay/bot/core.py:17` against `reelay/cloud/job.py:9`, plus the relay's copy at `relay/app.ts:34`.
- **Cost:** MEASURED parser output: a failed fetch for each affected link.

| Message | Local bot finds | Cloud finds |
|---|---|---|
| `watch (https://youtu.be/abc)` | `https://youtu.be/abc)` | `https://youtu.be/abc` |
| `https://youtu.be/abc.` | `https://youtu.be/abc.` | `https://youtu.be/abc` |
| `HTTPS://youtu.be/abc` | nothing | `HTTPS://youtu.be/abc` |

- **Fix:** have the local bot use `first_url` from `reelay/cloud/job.py`, and re-run the bot tests.
- **Risk:** local path only. If the demo does use local mode, this becomes AFTER.

### 7. The Adopt line check differs between relay and Python: LOW
- **Where:** `relay/app.ts:11-12` against `repo_parts` used by `reelay/core/reports.py:58-60`. `docs/CLOUD-SPEC.md:92` documents an older, looser pattern.
- **What happens:** MEASURED for Python: it rejects `o/foo.git` and `o/.github`. READ for the relay: its pattern accepts both. Normal runs can't hit this, because the report step only sends lines Python approved.
- **Cost:** no time cost. The risk is the two rules drifting further apart.
- **Fix:** tighten the relay's pattern to match Python, add a test and correct the spec line.
- **Risk:** low, but the payoff is small too. Fine to skip tonight.

### 8. A full hourly cap makes each sweep churn: AFTER
- **Where:** `relay/app.ts:400-410`, `fireReady`.
- **Cost:** MEASURED by the sweep agent: 16 KV calls per sweep with 5 ready jobs and the cap full. It only happens at 20 runs in an hour.
- **Fix:** check the rate slot before claiming the row.
- **Risk:** changes job claiming for very little gain.

### 9. `bundle.json` gives the model a frame list it doesn't need: AFTER
- **Where:** `routine/PROMPT.md:18` has the model read `bundle.json`; `frames_to_read` already names the paths.
- **Cost:** MEASURED by the sweep agent: about 2.4 KB, about 590 tokens per run with 30 frames.
- **Fix:** give the model a slim copy and keep the full one for the report step.
- **Risk:** changes a file the prepare and report steps share.

### 10. The clone size check rescans the whole tree every 0.25 s: AFTER
- **Where:** `reelay/cloud/report.py:117-119`.
- **Cost:** MEASURED by the sweep agent: 124 ms per scan on a 20,000-file tree. ESTIMATED: the fixed sleep adds about 0.125 s per clone.
- **Fix:** replace the sleep with `proc.wait(timeout=0.25)`. Do not size only `.git`, as a first draft of this analysis suggested: the working tree counts toward the 50 MB limit, so that would weaken it.
- **Risk:** this is the safety limit, for a very small saving.

## Checked and fine
- **`sweep()` on each webhook:** one KV list on an empty queue (measured). Not a real slowdown.
- **Mac polling:** 2 KV lists plus 1 write per empty poll (measured). Not worth touching.
- **Mac bundle storage:** 23 sequential KV writes for 20 frames, about 0.35 s estimated on remote KV. Mac-fetched jobs only.
- **Frame extraction:** 1.3 s of scene detection on a 120 s clip, and 3.2 s for 30 frames (measured).
- **Bounded already:** yt-dlp and ffmpeg timeouts, the Python relay client's 60 s timeout and read caps, sandbox safety scan (26 ms on 20,000 files), rate-slot and report-budget retries.
- **Relay and Python agree on:** URL character class, GitHub owner/repo parsing, and the frame and text size limits.

## Outside this brief
The Adopt gate runs only in the report step. The VM can reach `/api/send`, so a model tricked by a hostile video could post a valid clone line and get Adopt/Skip buttons without passing the gate. The relay checks the line's format only. READ, not reproduced. The impact is limited because Adopt on `main` only prints a clone line, but it matters more if T32's install ever lands. AFTER.
