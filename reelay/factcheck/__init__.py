"""Fact-check: VideoBundle + Profile -> Verdict (summary, checked claims, candidates).

Public entry point: `fact_check(bundle, profile, ...)`. It never raises.

Two interchangeable backends (config key `factcheck_backend`, default "claude-code"):
- "claude-code": `claude -p` with your normal login (no extra cost on Claude Pro).
  Tools are only Read (frames folder) and WebSearch. No Bash/Write/Edit/WebFetch.
- "api": the Anthropic SDK with the frames as images and the web search tool.
  Needs ANTHROPIC_API_KEY.

Safety model: everything from the video (transcript, caption, frames) and the
profile is untrusted data. It goes into a delimited block the model is told never
to obey, and the model's JSON answer is validated strictly (see parse.py). A
candidate is only a suggestion: nothing here installs, clones or runs anything.
The summary and claim text have URLs stripped (sources/candidates carry vetted ones).
T8 MUST send them as plain text, never with Markdown/HTML parse mode.
"""

from reelay.factcheck.check import fact_check

__all__ = ["fact_check"]
