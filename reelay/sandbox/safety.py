"""Pre-run checks on a fetched repo. Anything found here means: do not run it."""

import re
from pathlib import Path

MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
# A `hooks:` key in markdown frontmatter, or a hooks entry in a settings json,
# is a shell command that Claude Code itself would run, outside tool limits.
_FRONTMATTER_HOOKS = re.compile(r"^hooks\s*:", re.MULTILINE)


def _walk(repo: Path):
    for p in repo.rglob("*"):
        if ".git" in p.relative_to(repo).parts:
            continue
        yield p


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    return text.split("---", 2)[1] if text.count("---") >= 2 else text


def find_problem(repo: Path) -> str | None:
    """Return a short reason the repo must not be run, or None if it looks OK."""
    files = total = 0
    for p in _walk(repo):
        if p.is_symlink():
            return f"contains a symlink ({p.relative_to(repo)})"
        if not p.is_file():
            continue
        files += 1
        total += p.stat().st_size
        if files > MAX_FILES or total > MAX_BYTES:
            return "too large to trial (too many files or over 50 MB)"
        name = p.name.lower()
        try:
            if name.endswith(".md") and _FRONTMATTER_HOOKS.search(_frontmatter(p.read_text(errors="ignore"))):
                return f"declares hooks in frontmatter ({p.relative_to(repo)})"
            if name.startswith("settings") and name.endswith(".json") and '"hooks"' in p.read_text(errors="ignore"):
                return f"settings file declares hooks ({p.relative_to(repo)})"
        except OSError:
            return f"unreadable file ({p.relative_to(repo)})"
    return None
