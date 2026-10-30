# Reelay security

This describes what the code and the peer reviews show. "Not verified live" means tests pass with fakes but the real service/CLI behaviour was not exercised. Reviews referenced: [`docs/reviews/`](reviews/).

## Threat model

| Asset | What an attacker wants |
|---|---|
| Your machine and files (`~/.ssh`, other projects) | read/write/execute through the AI tool |
| Telegram bot token, API keys (`~/.reelay/config.toml`) | steal them |
| Your Claude Code session | steer it with a tricked instruction |
| Your disk/time/money | huge downloads, runaway trials |

**Untrusted input:** (1) video content (caption, transcript, on-screen text); (2) anything a stranger sends to the bot; (3) the repo/skill being trialled; (4) text the AI model itself returns (it may be echoing (1)); (5) URLs from any of those.
**Trusted:** you, typing in your own paired chat, and your config file.
**Out of scope:** a compromised machine or Telegram account; kernel-level escapes (the sandbox is not a VM).

## Protections

### 1. Video is data, never instructions
- Fact-check prompt puts caption, transcript and profile text in a random-nonce delimited block, control characters stripped, sizes capped (T6).
- `Job.text`, forwarded text, and video text never reach the router/adapter; the adapter only gets the router's output for a message you typed (T3 FIX 3 → forwarded text creates no job; T8 test `test_video_text_is_data_never_an_instruction`; T5: task goes on stdin, skill/model regex-validated, can't start with `-`).
- Quiz: forwarded text can't answer a quiz question and land in your profile/prompts (T3 review of T8, FIX 2).

### 2. Only the paired owner can use the bot
- 8-char one-time code, 15 min expiry, consumed on use, 5 wrong tries per user then lock (in memory, resets on restart); stored `0600` in a `0700` folder (T1 review of T3, NIT 1–2).
- **BLOCKER (T1→T3):** attachments were downloaded *before* the pairing check. Fixed: strangers get no reply and no download. **BLOCKER:** sender-controlled filename could escape the temp dir. Fixed: file saved as `<file_unique_id>.mp4`.
- Adopt/Skip taps must belong to the report's owner and only the first tap counts (T3 FIX 4 → T8 `decide`).

### 3. Nothing is installed or edited without you
- Adopt only records to `~/.reelay/decisions.jsonl` and prints a command; it never installs.
- **BLOCKER (T3→T8):** the printed `git clone` line used a URL from the model/video; `--upload-pack=…` or `ext::…` would run code if pasted. Fixed: the line appears only for `https://github.com/<owner>/<repo>`, as `git clone -- <url> <safe-folder>`; 14 hostile URLs tested.
- Instructions show a confirm card (task, project, skill, model, effort) with Run it / Cancel.

### 4. The adapter can only act inside allow-listed projects
- `project` must resolve (symlinks, `..`) inside `allowed_projects`; no list → refuse; `/` or bare home as an entry → refused (T5 FIX 2).
- Permission mode defaults to read-only `plan`; `bypassPermissions`/`auto` are refused (T5).
- Timeouts kill the whole process group (T4 FIX 3 on T5; T5 FIX on T4). `run()` never raises.
- **BLOCKER (T4→T5):** adapter and bot read `~/.reelay/config` in incompatible formats, so a bot token silently disabled the allow-list. Fixed: one shared TOML reader.

### 5. Sandbox trial (the riskiest part)
- Fetch: https only, no embedded credentials, no private hosts, `protocol.allow=never`, hooks disabled, `--` before the URL, minimal env, 50 MB / 2000-file caps.
- Any symlink → verdict `high`, never run (**BLOCKER, T5→T4**: a link to `~/.ssh` could be read). Any `hooks:` in skill frontmatter or settings → refused statically.
- **BLOCKER (T5→T4):** bare `--allowedTools Read,Write,Edit` were blanket rules for the whole disk. Replaced by `dontAsk` mode, a constant tool list with no Bash/web, path-scoped allow rules for the work folder only, deny rules for the real `~/.reelay ~/.ssh ~/.claude ~/.aws ~/.config`.
- **BLOCKER (T5→T4):** skill hooks run as shell outside tool limits. Mitigated in depth: `--bare`, `disableAllHooks`, static refusal. **Not verified live**: the canary-file test is still owed.
- Scan (skill-scan) result `high` is never run; scanner missing/broken → `review`, never `clean`.
- Run folder is a `0700` temp dir, deleted afterwards. Own HOME and `CLAUDE_CONFIG_DIR`, so your Claude login and keys aren't present; live runs need an API key (verified live: Pro login doesn't carry over). Without one: scan-only, stated in the report.
- `trial_model` from config is validated (cannot become a CLI flag).
- **Honest limit:** folder isolation, same OS user, not a VM.

### 6. Fact-check lockdown
- Tools: `Read` (frame folder only) and `WebSearch`. Bash/Write/Edit/Task denied; WebFetch removed (T7 review FIX: it could be steered to local services or leak profile goals in a URL). `dontAsk`, `--strict-mcp-config`, empty temp cwd, `--setting-sources project --disable-slash-commands` (accepted by the CLI; not proven live).
- Model output is strictly validated: unknown verdict → `unverified`; "true/false/misleading" without a valid source → `unverified`; caps on claims/sources.
- URLs fail closed (T7 FIX): dotted public names only, port 443, no IP literals, `.internal/.local/.lan…` blocked, must resolve to global addresses, `\` rejected. Links are stripped from summary/claim text; reports are sent as plain text with link previews off (T3 previews branch).

### 7. Download limits
Private/loopback/link-local hosts rejected before yt-dlp (T2 FIX 1); ≤ 200 MB and ≤ 10 min (T2 FIX 2); Telegram files > 20 MB get a friendly reply. A local `source` is only ever a file the bot itself saved.

### 8. Installer (T9)
- Token and API key are read with hidden input (`getpass`), never printed, never in prompts, the plist or error text; the Telegram lookup raises argument-less exceptions because its URL contains the token. Tests assert this.
- Config is written atomically as `0600` in a `0700` folder; the old file is kept as `config.toml.bak`, also `0600`, created fresh (T7 FIX: backups were briefly world-readable; `~/.reelay` is tightened even if it existed). The wizard and `reelay doctor` warn that the backup still holds old secrets (T7 FIX).
- **Folder deny-list (T7 FIX):** the allow-list refuses `/`, Home, any parent of Home, anything inside `~/.ssh ~/.aws ~/.gnupg ~/.config ~/.reelay ~/Library` and system roots (`/etc /System /usr /var` …), after resolving symlinks.
- Optional launchd service is off unless you say yes; the plist holds only the command, absolute PATH entries and the log path; `reelay.log` is `0600`. Never runs remote scripts or Homebrew for you (it prints those commands); `install.sh` refuses root and the unset-URL placeholder. Not verified live: real `launchctl`, login/restart, a real BotFather token and QR scan, the real `curl | sh`.

### 9. Secrets and privacy
- Token and keys only in `~/.reelay/config.toml`, never in code/git; `ConfigError` shows the path, not contents. httpx logs (which contain the token in URLs) are pinned to WARNING.
- Profile: `0600`, size-bounded, `/forget` deletes it. Goals and vibe are sent to Claude when it checks a video (T6 FIX, reflected in `/profile` wording). Whisper via Groq is opt-in and uploads audio.

## Known gaps
No VM/container; no live verification of the trial permission rules, `--bare` hook blocking, Telegram, or the router API call; DNS rebinding/redirects are only partly covered (`is_private_host` checks only the first hop, and yt-dlp follows redirects, so a public URL that redirects to a private address is not caught); the pairing lockout resets on restart; `install.sh` follows `main` unpinned until a release tag exists; `~/.reelay/config.toml` should be `chmod 600` (done by the installer).
