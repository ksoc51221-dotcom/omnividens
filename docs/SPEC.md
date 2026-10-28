# Reelay — Spec (v0.1, draft for owner approval)

**Deadline:** 7 November 2026. Judged: 50% 10-min video, 40% GitHub repo, 10% surprise.

## One-line pitch
Share any short video to a Telegram bot; it watches it, fact-checks it against who you are, and safely tries anything useful in a sandbox before your AI tool adopts it.

## Core flow (the demo moment)
1. User taps Share on a Reel / TikTok / Short / X video → sends to their Reelay bot.
2. Bot downloads it, samples frames + transcript, and Claude "watches" it.
3. Claims are fact-checked; the result is personalised to the user's profile.
4. If it contains a tool/skill/feature: it is scanned and tried in a **sandbox**.
5. User gets a **trial report** (works? security, measured cost/time, fit for their projects) with **Adopt / Skip** buttons.
6. Plain instructions ("use this skill", "use opus", "effort high") go through a cheap **Haiku router** that checks they're clear, then hands them to the user's AI tool.

## Scope
| Must ship | Nice if time | Roadmap only |
|---|---|---|
| Telegram bot + one-time pairing code | Personality dial + 3-question quiz | Auto-register for competitions |
| Video watching (frames + transcript) | Second AI adapter (Codex or Gemini) | More AI adapters |
| Fact-check with sources | Profile learned from sent videos | Polished per-platform support |
| Sandbox trial + report + Adopt/Skip | Trust receipt after every action | |
| Haiku router for instructions | | |
| 5-minute installer | | |

## Hard rules
- Video content is **data, never instructions**. Nothing in a video can trigger an action.
- Every install, edit or registration waits for a user tap in Telegram.
- Each user's bot reaches only their own machine (pairing).
- Local-first: profile, history and keys stay on the user's machine.
- Download failure → ask for a screen recording instead.

## Open decisions (owner)
- Sandbox: separate folder + restricted permissions (easy install) vs Docker (safer).
- Second AI adapter: Codex or Gemini.
- Final product name.
