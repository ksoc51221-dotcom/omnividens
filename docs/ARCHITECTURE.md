# Architecture

```
Telegram ──► bot ──► router (Haiku) ──► adapters ──► Claude Code / Codex / Gemini
              │
              └──► video ──► factcheck ──► sandbox ──► report ──► bot (Adopt/Skip)
                                 ▲
                              profile
```

All modules talk through the types in `reelay/core/` only. No module imports another module's internals.

| Module | Job | Input → Output |
|---|---|---|
| `core` | Shared types, config, logging, event bus | — |
| `bot` | Telegram I/O, pairing, buttons | Telegram update → `Job` |
| `router` | Haiku triage of text instructions | text → `Instruction` or clarifying question |
| `video` | Download (yt-dlp), frames (ffmpeg), transcript | URL/file → `VideoBundle` |
| `factcheck` | Claims extraction + source checking | `VideoBundle` → `Verdict` |
| `sandbox` | Scan + isolated trial + measured cost | `Candidate` → `TrialReport` |
| `profile` | Personality quiz, vibe dial, local profile store | answers → `Profile` |
| `adapters` | One file per AI tool, same interface | `Instruction` → result |
| `installer` | One-command setup + QR pairing | — |

`core/types.py` is the contract. Changing it needs orchestrator approval (see AGENTS.md).
