"""Sandbox: fetch a candidate, scan it, try it in isolation, measure it -> TrialReport.

Public entry point: `run_trial(candidate, runner=None, ...)`.

What "isolated" means here (honest version, folder isolation is NOT a VM):
- The repo is cloned into a fresh private (0700) folder in the system temp dir.
- Repos with symlinks, hooks, too many files or over 50 MB are never run.
- Nothing from the repo is run by us. Claude Code is started with its own
  HOME and CLAUDE_CONFIG_DIR inside that folder, so the user's real skills,
  settings, memory and projects are not loaded.
- Claude runs with --bare (no hooks, no keychain), "disableAllHooks", file tools
  only (no Bash, no web), allow rules scoped to the work folder, deny rules for
  ~/.reelay, ~/.ssh, ~/.claude, a hard time limit (process group is killed).
- Auth: your Claude login (OAuth/keychain) does NOT carry into the isolated HOME,
  verified: "Not logged in". Trials therefore need an API key (ANTHROPIC_API_KEY
  or anthropic_api_key in ~/.reelay/config.toml). Without one, nothing is run.
- The path-scoped rule syntax and the hook blocking were written from the docs
  and --help; they could not be tested live without an API key.

What it does NOT stop: Claude Code itself runs as the same OS user, so a bug
in it or a malicious skill that tricks it could still read files this user can
read. The static scan (step 2) and the 'high' = never-run rule are the main
defences. Use a real VM or container for untrusted code you care about.
"""

from reelay.sandbox.trial import run_trial

__all__ = ["run_trial"]
