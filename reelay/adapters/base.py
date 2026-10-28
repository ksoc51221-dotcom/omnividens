"""Shared adapter interface and safety checks.

An adapter hands an `Instruction` (from the router) to the user's own AI tool.
To add a tool (Codex, Gemini...): write one file with a subclass of `Adapter`
and add it to `ALL_ADAPTERS` in `registry.py`.
"""

import re
import subprocess
import os
import signal
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reelay.core.types import Instruction

DEFAULT_TIMEOUT_S = 600

# A runner takes (argv, cwd, stdin_text, timeout) and returns a CompletedProcess.
# Tests pass a fake; the real one is `subprocess_runner`.
Runner = Callable[[list[str], Path, str, int], subprocess.CompletedProcess]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")  # can't start with "-"


@dataclass
class AdapterResult:
    ok: bool
    text: str = ""
    tokens: int = 0
    seconds: float = 0.0
    error: str = ""


class Adapter(ABC):
    name: str = ""

    @abstractmethod
    def available(self) -> bool:
        """True if this tool is installed on this machine."""

    @abstractmethod
    def run(self, instruction: Instruction) -> AdapterResult:
        """Run the instruction. Must never raise; report problems in the result."""


def subprocess_runner(argv: list[str], cwd: Path, stdin_text: str, timeout: int) -> subprocess.CompletedProcess:
    """Run argv in its own process group; on timeout kill the whole group."""
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def check_project(project: Path | None, config: dict) -> Path:
    """Return the resolved project folder, or raise ValueError.

    The folder must be inside one of config['allowed_projects']. With no
    allow-list, or no project, we refuse: the router's path is untrusted.
    """
    if project is None:
        raise ValueError("no project folder given")
    allowed = [Path(p).expanduser().resolve() for p in config.get("allowed_projects", [])]
    too_wide = {Path("/").resolve(), Path.home().resolve()}
    if any(a in too_wide for a in allowed):
        raise ValueError("allowed_projects may not contain / or your whole home folder")
    if not allowed:
        raise ValueError("no allowed_projects set in ~/.reelay/config")
    target = Path(project).expanduser().resolve()  # resolves symlinks and ..
    if not target.is_dir():
        raise ValueError(f"project folder does not exist: {target}")
    if not any(target == a or a in target.parents for a in allowed):
        raise ValueError(f"project folder is not in allowed_projects: {target}")
    return target


def check_name(value: str | None, what: str) -> str | None:
    if value is not None and not _NAME.match(value):
        raise ValueError(f"bad {what}: {value!r}")
    return value
