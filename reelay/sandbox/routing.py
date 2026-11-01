"""Which backend runs a trial: light skills go to local Ollama, heavy ones to Claude.

Everything here is a pure function or a tiny file, so it is tested with fakes.
All thresholds live in this one block; change them here only.
"""

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from reelay.core.config import reelay_home

# ---- thresholds (one place) -------------------------------------------------
LIGHT_MAX_FILES = 5
LIGHT_MAX_BYTES = 20 * 1024
LIGHT_MAX_SKILL_LINES = 150
CODE_SUFFIXES = frozenset({
    ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".go",
    ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".php", ".pl",
    ".lua", ".r", ".ps1", ".bat", ".sql",
})
DEFAULT_MAX_CLAUDE_TRIALS_PER_DAY = 5
DEFAULT_MAX_TRIAL_USD = 0.05
DEFAULT_LOCAL_MODEL = "qwen3:8b"
LOCAL_MIN_TIMEOUT_S = 600  # measured: a tiny skill took ~295 s on qwen3:8b; Claude backends keep the caller's limit
OLLAMA_URL = "http://localhost:11434"  # fixed on purpose: local trials never leave this Mac
BACKENDS = ("auto", "subscription", "local", "api")
CLAUDE_BACKENDS = ("subscription", "api")
USAGE_FILE = "trial_usage.json"

LOCAL_CAVEAT = "light model, may understate"


# ---- light / heavy ----------------------------------------------------------
def classify(repo: Path) -> tuple[bool, str]:
    """(is_light, why). Light = tiny, text-only, a single short SKILL.md."""
    files = [p for p in repo.rglob("*")
             if ".git" not in p.relative_to(repo).parts and p.is_file() and not p.is_symlink()]
    if len(files) > LIGHT_MAX_FILES:
        return False, f"{len(files)} files (light is up to {LIGHT_MAX_FILES})"
    size = sum(p.stat().st_size for p in files)
    if size > LIGHT_MAX_BYTES:
        return False, f"{size // 1024} KB of text (light is up to {LIGHT_MAX_BYTES // 1024} KB)"
    code = sorted(p.name for p in files if p.suffix.lower() in CODE_SUFFIXES)
    if code:
        return False, f"contains code ({code[0]})"
    skills = [p for p in files if p.name.lower() == "skill.md"]
    if len(skills) != 1:
        return False, "no single SKILL.md" if not skills else "several SKILL.md files"
    lines = len(skills[0].read_text(errors="ignore").splitlines())
    if lines > LIGHT_MAX_SKILL_LINES:
        return False, f"SKILL.md has {lines} lines (light is up to {LIGHT_MAX_SKILL_LINES})"
    return True, f"{len(files)} small text file(s), SKILL.md {lines} lines"


def choose_backend(
    repo: Path,
    available: dict[str, bool],
    claude_used_today: int,
    cap: int = DEFAULT_MAX_CLAUDE_TRIALS_PER_DAY,
    configured: str = "auto",
    local_blocked: str = "",
) -> tuple[str, str]:
    """Pick (backend, reason). backend is "subscription", "api", "local" or "none" (scan-only).

    `available` maps backend name -> usable now. Claude backends are skipped once
    `claude_used_today` reaches `cap`. A non-empty `local_blocked` (a reason) means local must not
    be used even if it works, e.g. this video already had its one local trial.
    """
    under_cap = claude_used_today < cap
    claude = next((b for b in CLAUDE_BACKENDS if available.get(b)), None)
    local = available.get("local", False) and not local_blocked
    no_local = local_blocked or "Ollama is not usable"
    capped = f"daily Claude cap of {cap} reached"

    if configured in ("subscription", "api"):  # the owner picked one: no silent switching
        if not available.get(configured):
            return "none", f"trial_backend is {configured} but it is not set up"
        if not under_cap:
            return "none", f"{capped}; trial_backend is {configured}"
        return configured, f"trial_backend is {configured}"
    if configured == "local":
        return ("local", "trial_backend is local") if local else ("none", f"trial_backend is local but {no_local}")

    light, why = classify(repo)
    if light:
        if local:
            return "local", f"light skill ({why}); local keeps it on this Mac and free"
        if claude and under_cap:
            return claude, f"light skill ({why}) but {no_local}; using Claude"
        if claude:
            return "none", f"light skill but {no_local} and {capped}"
        return "none", f"light skill but {no_local} and no Claude backend is set up"
    if claude and under_cap:
        return claude, f"heavy skill ({why}); using Claude"
    if local:
        extra = capped if claude else "no Claude backend configured"
        return "local", f"heavy skill ({why}) but {extra}; ran on a small local model ({LOCAL_CAVEAT})"
    return "none", f"heavy skill ({why}) and no backend usable" + (f" ({capped})" if claude else "")


# ---- daily Claude counter ---------------------------------------------------
def _usage_path() -> Path:
    return reelay_home() / USAGE_FILE


def claude_used_today(today: date | None = None) -> int:
    today = today or date.today()
    try:
        data = json.loads(_usage_path().read_text())
        return int(data["count"]) if data.get("date") == today.isoformat() else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0


def record_claude_trial(today: date | None = None) -> int:
    """Count one Claude-backed trial for today; returns the new count. File is 0600."""
    today = today or date.today()
    count = claude_used_today(today) + 1
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-")  # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"date": today.isoformat(), "count": count}, f)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
    return count
