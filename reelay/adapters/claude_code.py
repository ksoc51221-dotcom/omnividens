"""Claude Code adapter: runs `claude -p` headless in an allow-listed project.

Flags (checked against `claude --help`, v2.1.x): -p/--print, --output-format json,
--model, --effort, --permission-mode, --no-session-persistence.
The task goes in on stdin, so it can never be read as a flag.
"""

import json
import shutil
import subprocess
import time

from reelay.core.config import ConfigError, load_config
from reelay.core.types import Instruction

from .base import (DEFAULT_TIMEOUT_S, Adapter, AdapterResult, Runner, check_name,
                   check_project, subprocess_runner)

EFFORTS = {"low", "medium", "high", "xhigh", "max"}
# "bypassPermissions" and "auto" are deliberately absent: config can't enable them.
SAFE_MODES = {"plan", "dontAsk", "acceptEdits"}
DEFAULT_MODE = "plan"  # read-only: Claude can look but not change anything


class ClaudeCodeAdapter(Adapter):
    name = "claude-code"

    def __init__(self, runner: Runner = subprocess_runner, config: dict | None = None,
                 binary: str | None = None):
        self._runner = runner
        self._config = config  # None -> read ~/.reelay/config on each run
        self._binary = binary

    def available(self) -> bool:
        return (self._binary or shutil.which("claude")) is not None

    def build_argv(self, instruction: Instruction, config: dict) -> tuple[list[str], str]:
        """Return (argv, stdin_text). Raises ValueError on anything unsafe."""
        task = instruction.task.strip()
        if not task:
            raise ValueError("empty task")
        if task.startswith("/"):
            raise ValueError("task may not start with '/' (would run a slash command)")
        mode = config.get("permission_mode", DEFAULT_MODE)
        if mode not in SAFE_MODES:
            raise ValueError(f"permission_mode not allowed: {mode!r}")
        if instruction.effort is not None and instruction.effort not in EFFORTS:
            raise ValueError(f"bad effort: {instruction.effort!r}")
        model = check_name(instruction.model, "model")
        skill = check_name(instruction.skill, "skill")

        argv = [self._binary or shutil.which("claude") or "claude", "-p",
                "--output-format", "json", "--permission-mode", mode,
                "--no-session-persistence"]
        if config.get("max_budget_usd"):
            argv += ["--max-budget-usd", str(float(config["max_budget_usd"]))]
        if model:
            argv += ["--model", model]
        if instruction.effort:
            argv += ["--effort", instruction.effort]
        return argv, (f"/{skill} {task}" if skill else task)

    def run(self, instruction: Instruction) -> AdapterResult:
        try:
            config = self._config if self._config is not None else load_config()
        except ConfigError as e:
            return AdapterResult(ok=False, error=f"refused: {e}")
        try:
            project = check_project(instruction.project, config)
            argv, stdin_text = self.build_argv(instruction, config)
        except ValueError as e:
            return AdapterResult(ok=False, error=f"refused: {e}")
        timeout = int(config.get("timeout_s", DEFAULT_TIMEOUT_S))
        start = time.monotonic()
        try:
            proc = self._runner(argv, project, stdin_text, timeout)
        except subprocess.TimeoutExpired:
            return AdapterResult(ok=False, seconds=time.monotonic() - start,
                                 error=f"timed out after {timeout}s")
        except OSError as e:
            return AdapterResult(ok=False, error=f"could not start claude: {e}")
        return self._parse(proc, time.monotonic() - start)

    @staticmethod
    def _parse(proc: subprocess.CompletedProcess, seconds: float) -> AdapterResult:
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            data = None
        if not isinstance(data, dict):
            return AdapterResult(ok=False, seconds=seconds,
                                 error=str(proc.stderr or proc.stdout or "no output")[:500])
        usage = data.get("usage")
        try:
            tokens = sum(int(usage.get(k) or 0) for k in
                         ("input_tokens", "output_tokens",
                          "cache_creation_input_tokens", "cache_read_input_tokens"))
        except (ValueError, TypeError, AttributeError):
            tokens = 0
        failed = proc.returncode != 0 or bool(data.get("is_error"))
        text = str(data.get("result", ""))
        return AdapterResult(ok=not failed, text=text, tokens=tokens, seconds=seconds,
                             error=text[:500] if failed else "")
