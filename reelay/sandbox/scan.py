"""Step 2: static scan by calling the skill-scan script (never copied, never imported)."""

import json
import subprocess
import sys
from pathlib import Path

SCAN_SCRIPT = Path("~/.claude/skills/skill-scan/scan.py").expanduser()
_RANK = {"clean": 0, "review": 1, "high": 2}
# skill-scan's own verdicts -> ours. LOW findings are weak signals, treated as clean.
_MAP = {"CLEAN": "clean", "LOW": "clean", "REVIEW": "review", "HIGH": "high"}


def map_verdict(rows: list[dict]) -> str:
    """Worst verdict across all scanned units. Unknown values count as 'review'."""
    worst = "clean"
    for row in rows:
        ours = _MAP.get(str(row.get("verdict")), "review")
        if _RANK[ours] > _RANK[worst]:
            worst = ours
    return worst


def scan(path: Path, timeout_s: int = 60) -> tuple[str, str]:
    """Return (verdict, note). If the scanner can't run we say 'review', never 'clean'."""
    if not SCAN_SCRIPT.exists():
        return "review", "skill-scan not installed; candidate not scanned"
    cmd = [sys.executable, "-I", str(SCAN_SCRIPT), "--json", "--all", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        rows = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return "review", "skill-scan failed or timed out"
    if not isinstance(rows, list) or not rows:
        return "review", "skill-scan found no skill or plugin to judge"
    findings = sum(len(r.get("findings", [])) for r in rows)
    return map_verdict(rows), f"skill-scan: {len(rows)} unit(s), {findings} finding(s)"
