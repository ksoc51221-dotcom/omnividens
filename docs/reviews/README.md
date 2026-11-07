# Peer reviews

Each build task was written by one Sonnet agent and reviewed by a different one before the orchestrator merged it (rules in [AGENTS.md](../../AGENTS.md)). A review ends in a verdict (BLOCKER / FIX / NIT counts); blockers had to be fixed before merge. These files are kept as a record of that process. The threat-model summary built from them is [docs/SECURITY.md](../SECURITY.md).

Start with [T20-review.md](T20-review.md) (the cloud report gate, one blocker) or [code-analysis.md](code-analysis.md). Cross-module change requests between agents are in [REQUESTS.md](REQUESTS.md).

| File | Review |
|---|---|
| [T1-download-fix.md](T1-download-fix.md) | T1 download fix: review by T6 (agent/t1-download-fix @ 0dd86d1, read-only) |
| [T1.md](T1.md) | T1 review: video pipeline |
| [T2.md](T2.md) | T2 router: review file (reviewer: T3) |
| [T3.md](T3.md) | T3 review: Telegram bot (branch `agent/t3-bot`, reviewer: T1) |
| [T4.md](T4.md) | T4 review: Sandbox (branch `agent/t4-sandbox`, reviewer: T5) |
| [T5.md](T5.md) | T5 adapters: review file (reviewer: T4) |
| [T6-parse-fix.md](T6-parse-fix.md) | T6 parse-fix review (R-11, R-12) — branch `agent/t6-parse-fix` @ e9dee4f |
| [T6.md](T6.md) | T6 fact-check: review file |
| [T7.md](T7.md) | T7 Profile — review request (branch `agent/t7-profile`, reviewer: T6) |
| [T8.md](T8.md) | T8 review: pipeline (branch `agent/t8-pipeline`, worktree `../reelay-t8`, reviewer: T3) |
| [T9.md](T9.md) | T9 review: installer (branch `agent/t9-installer`, worktree `../reelay-t9`, reviewer: T7) |
| [T11.md](T11.md) | T11 review: README, DEMO, SECURITY (branch `agent/t11-docs`, reviewer: orchestrator) |
| [T12.md](T12.md) | T12 review: terminal mode (`reelay try`) |
| [T13.md](T13.md) | T13 review — `agent/t13-sandbox-sec` @ 34a6be5 (reviewer: T14 session) |
| [T14.md](T14.md) | T14 review (bug sweep: R-1, R-2, R-3, R-9) — branch `agent/t14-bugsweep` (601a334, 096f9db, 2adad12) |
| [T15.md](T15.md) | T15 review — `agent/t15-trial-backends` @ 3bac6a7 (reviewer: T13 session, Sonnet 5.5) |
| [T16-T19.md](T16-T19.md) | Review: T19 (`2b367f9`) + T16 (`491a34a`) on `agent/t16-rename` |
| [T20-review.md](T20-review.md) | T20 review (cloud reports) by T8: `agent/t20-reports` @ 645e5bc |
| [T20.md](T20.md) | T20 review: one fact-check + one report per candidate (cloud path) |
| [T22.md](T22.md) | T22 bug sweep |
| [T23a.md](T23a.md) | T23a review notes: cloud relay |
| [T23b.md](T23b.md) | T23b review: cloud routine side |
| [T25-review.md](T25-review.md) | T25 review — `agent/t25-mac-fallback` @ 5f94ac8 (reviewer: T13 session, Sonnet 5.5) |
| [T25.md](T25.md) | T25 Mac fallback for blocked YouTube links (review request) |
| [T27.md](T27.md) | T27 review: top 5 bugs from the 5 Nov bug hunt |
| [T28.md](T28.md) | T28: five leftover bugs |
| [T29.md](T29.md) | T29 review: /help command |
| [T30.md](T30.md) | T30 review: Adopt only on safe skills, short skill reports |
| [T33-review.md](T33-review.md) | T33 review by T8 (`agent/t33-ux` @ 6559603) |
| [T34.md](T34.md) | T34: three low-risk fixes from the code analysis |
| [T37-review.md](T37-review.md) | T37 review — `agent/t37-report-name` @ d8763e1 (reviewer: T13 session, Sonnet 5.5) |
| [code-analysis.md](code-analysis.md) | Code analysis: slowdowns, hangs, duplicated logic |
