"""Fact-check: VideoBundle + Profile -> Verdict (summary, checked claims, candidates).

Public entry point: `fact_check(bundle, profile, ...)`. It never raises.

Two interchangeable backends (config key `factcheck_backend`, default "claude-code"):
- "claude-code": `claude -p` with your normal login (no extra cost on Claude Pro).
  Tools are only Read (frames folder), WebSearch and WebFetch. No Bash/Write/Edit.
- "api": the Anthropic SDK with the frames as images and the web search tool.
  Needs ANTHROPIC_API_KEY.

Safety model: everything from the video (transcript, caption, frames) and the
profile is untrusted data. It goes into a delimited block the model is told never
to obey, and the model's JSON answer is validated strictly (see parse.py). A
candidate is only a suggestion: nothing here installs, clones or runs anything.
Residual risk (honest): a model with web access that is tricked could still fetch
a URL of an attacker's choosing; it has no file-write or shell tool to do harm with.
"""

from reelay.factcheck.check import fact_check

__all__ = ["fact_check"]
