# Architecture

Two ways to run the same pipeline. **Cloud** is the main one (see [CLOUD-SPEC.md](CLOUD-SPEC.md) for why); **local mode** is the original design and still works.

## Cloud path (main)

```
Telegram ──► relay (Deno Deploy) ──► Claude Code routine (fresh VM per video)
   ▲             owner gate,               prepare: download, frames, transcript
   │             rate cap, KV              Claude: find repo, read it, write verdict
   └── reply + Skip/Adopt ◄── relay ◄──── report: code applies the safety gate, formats the message
```

The relay (`relay/app.ts`) is the only component with the bot token. The routine only gets a relay secret that the network proxy injects. The Adopt gate is code, not the model: the model can only lower a verdict (`apply_gate` in `reelay/core/reports.py`).

## Local mode (original design)

```
Telegram ──► bot ──► router (Haiku) ──► adapter ──► Claude Code
              │
              └──► video ──► factcheck ──► sandbox ──► report ──► bot (Adopt/Skip)
                                 ▲
                              profile
```

All modules talk through the types in `reelay/core/` only. No module imports another module's internals. `core/types.py` is the contract; changing it needed orchestrator approval (see [AGENTS.md](../AGENTS.md)).

| Module | Job | Input → Output |
|---|---|---|
| `relay/` (TypeScript) | Telegram webhook, owner gate, dedupe, rate cap, routine trigger | Telegram update → routine run |
| `cloud` | Routine side: prepare a job, send the report | job → `bundle.json` → Telegram message |
| `core` | Shared types, config, report gate and formatting | — |
| `video` | Download (yt-dlp), frames (ffmpeg), transcript | URL/file → `VideoBundle` |
| `factcheck` | Claims extraction + source checking | `VideoBundle` → `Verdict` |
| `sandbox` | Repo fetch, static scan, isolated trial (local mode) | candidate → report |
| `bot` | Local mode: Telegram I/O, pairing, buttons | Telegram update → `Job` |
| `router` | Local mode: Haiku triage of typed instructions | text → `Instruction` or clarifying question |
| `profile` | Local mode: quiz, vibe dial, profile store | answers → `Profile` |
| `adapters` | Runs a task in the owner's AI tool (Claude Code only) | `Instruction` → result |
| `installer` | Local mode: one-command setup + QR pairing | — |
