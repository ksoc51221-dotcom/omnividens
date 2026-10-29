# Cross-module change requests

Format: `- [open|done] from <task> → owner <task>: what you need and why`

- [done: branch agent/t3-previews] from T8 → owner T3: in `telegram_layer.py` send replies with link previews off (`link_preview_options=LinkPreviewOptions(is_disabled=True)` on `send_message` and `reply_text`), so URLs in fact-check text (T7 NIT on T6) don't unfurl. Also `filters.TEXT` already covers slash commands, so no handler change is needed for /vibe etc.
- [open] from T8 → orchestrator: no dependency changes needed (`python-telegram-bot>=21`, `anthropic` already listed). Tip for T9: `python -m reelay --pair` prints a new pairing code in the running process (codes made in another process are not seen by a running bot).
