"""The two ways to ask a model: `claude -p` (default) or the Anthropic API.

A backend is anything with `run(bundle, profile) -> str` returning the model's raw text.
It may raise; `fact_check` turns every error into a safe Verdict.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from reelay.adapters.base import check_name, subprocess_runner
from reelay.core.types import Profile, VideoBundle
from reelay.factcheck.prompt import build_prompt, frame_dirs, pick_frames

DEFAULT_TIMEOUT_S = 300
API_MODEL = "claude-sonnet-5-5"
WEB_SEARCH_TOOL = "web_search_20250305"
# The ceiling of tools for claude-code. Constant on purpose: never add Bash/Write/Edit.
CLAUDE_TOOLS = "Read,WebSearch,WebFetch"
DENIED_TOOLS = "Bash,Write,Edit,NotebookEdit,Task"


class Backend(Protocol):
    def run(self, bundle: VideoBundle, profile: Profile) -> str: ...


# (argv, cwd, stdin_text, timeout_s) -> CompletedProcess; tests pass a fake.
Runner = Callable[[list[str], Path, str, int], subprocess.CompletedProcess]


class ClaudeCodeBackend:
    def __init__(self, runner: Runner = subprocess_runner, timeout_s: int = DEFAULT_TIMEOUT_S,
                 model: str | None = None, binary: str | None = None):
        self._runner, self._timeout, self._binary = runner, timeout_s, binary
        self._model = check_name(model, "factcheck_model")

    def build_argv(self, dirs: list[Path]) -> list[str]:
        """Read-only access to the frame folders; web search/fetch; nothing else."""
        allow = [f"Read(//{d.as_posix().lstrip('/')}/**)" for d in dirs] + ["WebSearch", "WebFetch"]
        argv = [self._binary or shutil.which("claude") or "claude", "-p",
                "--output-format", "json", "--no-session-persistence",
                "--permission-mode", "dontAsk", "--strict-mcp-config",
                "--tools", CLAUDE_TOOLS, "--disallowedTools", DENIED_TOOLS,
                "--allowedTools", ",".join(allow)]
        for d in dirs:
            argv += ["--add-dir", str(d)]
        if self._model:
            argv += ["--model", self._model]
        return argv

    def run(self, bundle: VideoBundle, profile: Profile) -> str:
        frames = pick_frames(bundle)
        dirs = frame_dirs(frames)
        prompt = build_prompt(bundle, profile, frames, [str(f.image.resolve()) for f in frames])
        # Own empty folder as cwd, so no project CLAUDE.md or settings from elsewhere is picked up.
        with tempfile.TemporaryDirectory(prefix="reelay-fc-") as cwd:
            proc = self._runner(self.build_argv(dirs), Path(cwd), prompt, self._timeout)
        return result_text(proc)


def result_text(proc: subprocess.CompletedProcess) -> str:
    """The `result` field of `claude -p --output-format json`; raises on errors."""
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        data = None
    if not isinstance(data, dict):
        raise RuntimeError(str(proc.stderr or proc.stdout or "no output")[:200])
    if proc.returncode != 0 or data.get("is_error"):
        raise RuntimeError(str(data.get("result", "claude failed"))[:200])
    return str(data.get("result", ""))


class ApiBackend:
    """Anthropic SDK: frames as images, server-side web search tool."""

    def __init__(self, client=None, model: str = API_MODEL, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._client, self._model, self._timeout = client, check_name(model, "factcheck_model"), timeout_s

    def run(self, bundle: VideoBundle, profile: Profile) -> str:
        if self._client is None:
            import anthropic  # only needed for this backend
            self._client = anthropic.Anthropic(timeout=self._timeout)
        frames = pick_frames(bundle)
        prompt = build_prompt(bundle, profile, frames, [f"image {i + 1}" for i in range(len(frames))])
        content: list[dict] = []
        for f in frames:
            if f.image.stat().st_size > 4_500_000:
                continue
            kind = "image/png" if f.image.suffix.lower() == ".png" else "image/jpeg"
            data = base64.standard_b64encode(f.image.read_bytes()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": kind, "data": data}})
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5}]
        for _ in range(3):  # a long search turn may pause; let it continue a few times
            resp = self._client.messages.create(model=self._model, max_tokens=4000,
                                                messages=messages, tools=tools)
            if resp.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def make_backend(config: dict) -> Backend:
    name = config.get("factcheck_backend", "claude-code")
    timeout = int(config.get("factcheck_timeout_s", DEFAULT_TIMEOUT_S))
    model = config.get("factcheck_model")
    if name == "claude-code":
        return ClaudeCodeBackend(timeout_s=timeout, model=model)
    if name == "api":
        return ApiBackend(model=model or API_MODEL, timeout_s=timeout)
    raise ValueError(f"unknown factcheck_backend: {name!r} (use 'claude-code' or 'api')")
