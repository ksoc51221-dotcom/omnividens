"""Sandbox: fetch a candidate, scan it, try it in isolation, measure it -> TrialReport.

Public entry point: `run_trial(candidate, runner=None, ...)`.

What "isolated" means here (honest version, folder isolation is NOT a VM):
- The repo is cloned into a fresh folder under ~/.reelay/sandbox_runs/<id>/.
- Nothing from the repo is run by us. Claude Code is started with its own
  HOME and CLAUDE_CONFIG_DIR inside that folder, so the user's real skills,
  settings, memory and projects are not loaded.
- Claude gets only file tools (no Bash, no web tools) and a hard time limit.
- Only a minimal environment is passed (PATH + ANTHROPIC_API_KEY if set).

What it does NOT stop: Claude Code itself runs as the same OS user, so a bug
in it or a malicious skill that tricks it could still read files this user can
read. The static scan (step 2) and the 'high' = never-run rule are the main
defences. Use a real VM or container for untrusted code you care about.
"""

from reelay.sandbox.trial import run_trial

__all__ = ["run_trial"]
