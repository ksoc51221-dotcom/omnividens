# Task board

Status: `todo` → `doing (<session>)` → `review` → `approved` → `merged`.
Each task: owned files, what it needs first, who reviews it.

## Wave 1 — independent, run in parallel

| ID | Task | Owns | Needs | Reviewer | Status |
|---|---|---|---|---|---|
| T1 | Video pipeline: yt-dlp download (any platform), ffmpeg frames every ~2 s + scene cuts, subtitles or Whisper transcript, fallback message on failure → `VideoBundle` | `reelay/video/`, `tests/test_video.py` | — | T2 | merged |
| T2 | Haiku router: parse "use skill / model / effort" + task, ask a clarifying question if unclear → `Instruction` | `reelay/router/`, `tests/test_router.py` | — | T3 | merged |
| T3 | Telegram bot: receive links/files/text, one-time pairing code, Adopt/Skip buttons, map messages → `Job` | `reelay/bot/`, `tests/test_bot.py` | — | T4 | merged |
| T4 | Sandbox: isolated folder, static scan, try the candidate, measure tokens + time → `TrialReport` | `reelay/sandbox/`, `tests/test_sandbox.py` | — | T5 | review |
| T5 | Adapters: shared interface + Claude Code adapter (skill/model/effort flags) | `reelay/adapters/`, `tests/test_adapters.py` | — | T1 | merged |

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
| T6 | Fact-check: extract claims with timestamps, check with web sources, find candidates → `Verdict` | `reelay/factcheck/` | T1 | T4 | merged |
| T7 | Profile: 3-question quiz, vibe dial, local store, tone in replies | `reelay/profile/` | T3 | T2 | merged |
| T8 | Orchestration: wire bot → video → factcheck → sandbox → report, plus bot → router → adapter | `reelay/core/pipeline.py`, `reelay/__main__.py` | T1–T6 | T3 | merged |
| T9 | Installer: one command, detects AI tools, QR pairing, 5-minute setup | `installer/` | T3, T5 | T7 | merged |

## Wave 3 — polish

| ID | Task | Owns | Needs | Reviewer | Status |
|---|---|---|---|---|---|
| T10 | Second adapter (Codex or Gemini) | `reelay/adapters/<name>.py` | T5 | T5 | todo |
| T12 | Terminal mode: `reelay try <link/file/"instruction">` runs the full pipeline and prints replies, buttons as numbered choices; no Telegram | `reelay/terminal/`, `tests/test_terminal.py`, one dispatch line in `reelay/installer/cli.py` | T8, T9 | T8 | review |
| T13 | Bug sweep (security): R-6 skill `!`/allowed-tools rejection in sandbox scan, R-8 redirect note in SECURITY.md — from `BUG-REPORT-2026-10-30.md` §A | `reelay/sandbox/safety.py`, `tests/test_sandbox.py`, `docs/SECURITY.md` | T4 | T4 | review |
| T14 | Bug sweep (bot/pipeline/router): R-1 edited messages, R-2 no user, R-3 command-caption cleanup, R-9 small items | `reelay/bot/`, `reelay/core/pipeline.py`, `reelay/router/`, their tests | T3, T8, T2 | T3 | review |
| T11 | README, demo script, architecture diagram for judges | `README.md`, `docs/DEMO.md` | T8 | orchestrator | merged |
