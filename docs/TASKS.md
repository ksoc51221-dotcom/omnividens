# Task board

Status: `todo` → `doing (<session>)` → `review` → `approved` → `merged`.
Each task: owned files, what it needs first, who reviews it.

## Wave 1 — independent, run in parallel

| ID | Task | Owns | Needs | Reviewer | Status |
|---|---|---|---|---|---|
| T1 | Video pipeline: yt-dlp download (any platform), ffmpeg frames every ~2 s + scene cuts, subtitles or Whisper transcript, fallback message on failure → `VideoBundle` | `reelay/video/`, `tests/test_video.py` | — | T2 | review |
| T2 | Haiku router: parse "use skill / model / effort" + task, ask a clarifying question if unclear → `Instruction` | `reelay/router/`, `tests/test_router.py` | — | T3 | todo |
| T3 | Telegram bot: receive links/files/text, one-time pairing code, Adopt/Skip buttons, map messages → `Job` | `reelay/bot/`, `tests/test_bot.py` | — | T4 | todo |
| T4 | Sandbox: isolated folder, static scan, try the candidate, measure tokens + time → `TrialReport` | `reelay/sandbox/`, `tests/test_sandbox.py` | — | T5 | todo |
| T5 | Adapters: shared interface + Claude Code adapter (skill/model/effort flags) | `reelay/adapters/`, `tests/test_adapters.py` | — | T1 | todo |

### Skills to use (installed; don't install new ones without the owner's OK)
| Task | Skill | Use it for |
|---|---|---|
| T1 | `watch-video` (`~/.claude/skills/watch-video/scripts/`) | Proven download → frames → captions/Whisper pipeline. Reuse its approach; vendor code only if its LICENSE allows, with credit |
| T1 | `agent-reach` (`agent-reach read/subtitle <url>`) | Multi-platform links (YouTube, X, Reddit, Bilibili…) and subtitles |
| T2, T5 | `claude-api` | Current model IDs (Haiku for routing), structured output, SDK usage |
| T4 | `skill-scan` (`~/.claude/skills/skill-scan/scan.py`) | Static scan step of the sandbox; call it, don't copy it |
| T5 | `claude-code-guide` agent | Exact Claude Code CLI flags for model, effort, skills, headless runs |
| all | `engineering:testing-strategy` | Test plan for the module |

Searched GitHub for Telegram/video/sandbox skills: only 0–2 star repos, not used.

## Wave 2 — after wave 1 merges

| ID | Task | Owns | Needs | Reviewer | Status |
|---|---|---|---|---|---|
| T6 | Fact-check: extract claims with timestamps, check with web sources, find candidates → `Verdict` | `reelay/factcheck/` | T1 | T4 | todo |
| T7 | Profile: 3-question quiz, vibe dial, local store, tone in replies | `reelay/profile/` | T3 | T2 | todo |
| T8 | Orchestration: wire bot → video → factcheck → sandbox → report, plus bot → router → adapter | `reelay/core/pipeline.py`, `reelay/__main__.py` | T1–T6 | T3 | todo |
| T9 | Installer: one command, detects AI tools, QR pairing, 5-minute setup | `installer/` | T3, T5 | T7 | todo |

## Wave 3 — polish

| ID | Task | Owns | Needs | Reviewer | Status |
|---|---|---|---|---|---|
| T10 | Second adapter (Codex or Gemini) | `reelay/adapters/<name>.py` | T5 | T5 | todo |
| T11 | README, demo script, architecture diagram for judges | `README.md`, `docs/DEMO.md` | T8 | orchestrator | todo |
