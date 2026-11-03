"""Pre-run checks on a fetched repo. Anything found here means: do not run it."""

import re
from pathlib import Path

MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
# These are regex heuristics, not a YAML parser: they fail closed and may reject harmless markdown.
# A `hooks:` key in markdown frontmatter, or a hooks entry in a settings json,
# is a shell command that Claude Code itself would run, outside tool limits.
_KEY = r"^[ \t]*[\"']?{}[\"']?[ \t]*:"
_FRONTMATTER_HOOKS = re.compile(_KEY.format("hooks"), re.MULTILINE | re.IGNORECASE)
_ALLOWED_KEY = re.compile(_KEY.format("allowed-tools"), re.IGNORECASE)
# `!`cmd`` (or a ```! / ~~~! block) in a skill runs a shell command when the skill loads.
_DYNAMIC_SHELL = re.compile(r"!`|^[ \t]*(?:`{3,}|~{3,})!", re.MULTILINE)
_RISKY_TOOL = re.compile(r"\b(?:bash|webfetch|websearch)\b|mcp__", re.IGNORECASE)
_FM_BLOCK = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def _walk(repo: Path):
    for p in repo.rglob("*"):
        if ".git" in p.relative_to(repo).parts:
            continue
        yield p


def _normalize(text: str) -> str:
    return text.lstrip("\ufeff \t\r\n").replace("\r\n", "\n").replace("\r", "\n")


def _frontmatter(text: str) -> str:
    """The block between the opening `---` line and the next line that is exactly `---`.
    No such block (or no closing line) -> the whole text, so a hidden key is still seen."""
    m = _FM_BLOCK.match(text)
    return m.group(1) if m else text


def _risky_allowed_tools(front: str) -> bool:
    """True if an `allowed-tools` key (plus its list lines, blank lines included) names a risky tool."""
    lines, i = front.split("\n"), 0
    while i < len(lines):
        if _ALLOWED_KEY.match(lines[i]):
            block = [lines[i]]
            while i + 1 < len(lines) and (not lines[i + 1].strip() or lines[i + 1][0] in " \t-"):
                i += 1
                block.append(lines[i])
            if _RISKY_TOOL.search("\n".join(block)):
                return True
        i += 1
    return False


def find_problem(repo: Path) -> str | None:
    """Return a short reason the repo must not be run, or None if it looks OK."""
    files = total = 0
    for p in _walk(repo):
        if p.is_symlink():
            return f"contains a symlink ({p.relative_to(repo)})"
        if not p.is_file():
            continue
        files += 1
        try:
            total += p.stat().st_size
        except OSError:
            return f"unreadable file ({p.relative_to(repo)})"
        if files > MAX_FILES or total > MAX_BYTES:
            return "too large to trial (too many files or over 50 MB)"
        name = p.name.lower()
        try:
            if name.endswith(".md"):
                text = _normalize(p.read_text(errors="ignore"))
                front = _frontmatter(text)
                if _FRONTMATTER_HOOKS.search(front):
                    return f"declares hooks in frontmatter ({p.relative_to(repo)})"
                if _DYNAMIC_SHELL.search(text):
                    return f"skill asks to run shell commands when it loads ({p.relative_to(repo)})"
                if _risky_allowed_tools(front):
                    return f"skill asks for shell, web or MCP tools ({p.relative_to(repo)})"
            if name.startswith("settings") and name.endswith(".json") and '"hooks"' in p.read_text(errors="ignore"):
                return f"settings file declares hooks ({p.relative_to(repo)})"
        except OSError:
            return f"unreadable file ({p.relative_to(repo)})"
    return None
