"""reports: one short fact-check message plus one validated report per candidate (cloud and local share it).

Everything the routine hands us was produced after reading untrusted video and repo text, so each field
is validated here: unknown or missing values fail closed to read-only text with no `adopt_line`.
"""

import math
import re
from dataclasses import dataclass, field

from reelay.core.types import Verdict

TYPES = {"skill": "bot skill", "mod": "mod", "app": "full app"}  # fixed list; anything else is "unclear"
ADOPT_TYPES = {"skill", "mod"}
VERDICTS = ("YES", "NO", "UNSURE")
MAX_REPORTS = 9  # per video
MAX_TEXT = 3800
NO_PROJECTS = "No project list yet. Send /projects to add one."
NO_FIT = "No fit with your listed projects."
_REPO = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_LINK = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LEVELS = ("low", "medium", "high")
_ICONS = {"true": "✅", "false": "❌", "misleading": "⚠️", "unverified": "❔"}


def plain(value, limit: int) -> str:
    """Untrusted text -> one plain line: no control chars, no links, capped."""
    if not isinstance(value, str):
        return ""
    s = re.sub(r"\s+", " ", _LINK.sub("", _CTRL.sub(" ", value))).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def repo_parts(url) -> tuple[str, str] | None:
    m = _REPO.fullmatch(url) if isinstance(url, str) else None
    if not m or {m.group(1), m.group(2)} & {".", ".."} or m.group(2).endswith(".git"):
        return None
    return m.group(1), m.group(2)


def adopt_line_for(url) -> str | None:
    """`git clone -- <url>` for a plain github.com/owner/repo URL, else None."""
    return f"git clone -- {url}" if repo_parts(url) else None


@dataclass
class CandidateReport:
    name: str
    url: str
    type: str  # "skill" | "mod" | "app" | "unclear"
    type_note: str
    safety: str  # YES | NO | UNSURE
    safety_reason: str
    owner_repo: str | None  # exact owner/repo, only when it matches the URL
    name_only: bool  # matched from a name only, not a link the video showed
    what: str
    problem: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    impact: str = NO_FIT
    usage: dict = field(default_factory=dict)  # validated, every number labelled
    examples: list[str] = field(default_factory=list)
    ran: bool = False


def _strs(v, n: int, limit: int) -> list[str]:
    return [s for s in (plain(x, limit) for x in (v if isinstance(v, list) else [])[:n]) if s]


def _usage(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    mins = raw.get("setup_minutes")
    if isinstance(mins, (int, float)) and not isinstance(mins, bool) and math.isfinite(mins) and 0 <= mins < 10000:
        out["setup_minutes"] = round(mins)
    out["level"] = raw["level"] if isinstance(raw.get("level"), str) and raw["level"] in _LEVELS else None
    tok = raw.get("tokens")
    if isinstance(tok, int) and not isinstance(tok, bool) and 0 < tok < 10**9:
        out["tokens"] = tok
        # `measured` is only believed when the routine says so for a positive count; 0 or missing is never measured
        out["basis"] = "measured" if raw.get("basis") == "measured" else "estimate"
    out["money"] = plain(raw.get("money"), 60) or None
    return out


def _impact(raw, projects: list[str]) -> str:
    """Section 3. Only a project that is on the owner's written list may be named."""
    if not projects:
        return NO_PROJECTS
    if not isinstance(raw, dict):
        return NO_FIT
    wanted = plain(raw.get("project"), 80).lower()
    match = next((p for p in projects if p.lower() == wanted), None) if wanted else None
    why = plain(raw.get("why"), 300)
    return f"Fits {match}: {why}" if match and why else NO_FIT


def parse_report(raw, projects: list[str]) -> CandidateReport | None:
    """Validate one report object from the routine. None when it has no usable name and URL."""
    if not isinstance(raw, dict):
        return None
    name, url = plain(raw.get("name"), 80), raw.get("url")
    if not name or not isinstance(url, str) or len(url) > 500 or not url.startswith("https://"):
        return None
    typ = raw["type"] if isinstance(raw.get("type"), str) and raw["type"] in TYPES else "unclear"
    ident = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    parts = repo_parts(url)
    claimed = plain(ident.get("owner_repo"), 140).lower()
    owner_repo = f"{parts[0]}/{parts[1]}" if parts and claimed == f"{parts[0]}/{parts[1]}".lower() else None
    name_only = ident.get("name_only") is not False  # missing or odd means "name only": the careful reading
    safety_in = raw.get("safety") if isinstance(raw.get("safety"), dict) else {}
    safety = str(safety_in.get("verdict", "")).upper()
    safety = safety if safety in VERDICTS else "UNSURE"
    reason = plain(safety_in.get("reason"), 200)
    ran = raw.get("ran") is True
    if safety == "YES" and not (owner_repo and not name_only):
        safety, reason = "UNSURE", "I could not confirm this is the exact repo the video meant."
    if safety == "UNSURE" and not reason:
        reason = "I only read the code, I didn't run it."
    if safety == "NO" and not reason:
        reason = "The scan found high-risk patterns."
    info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
    return CandidateReport(
        name=name, url=url, type=typ, type_note=plain(raw.get("type_note"), 200), safety=safety,
        safety_reason=reason, owner_repo=owner_repo, name_only=name_only,
        what=plain(info.get("what"), 400) or "No description was found.", problem=plain(info.get("problem"), 200),
        pros=_strs(info.get("pros"), 2, 160), cons=_strs(info.get("cons"), 2, 160),
        impact=_impact(raw.get("impact"), projects), usage=_usage(raw.get("usage")),
        examples=_strs(info.get("examples"), 3, 160), ran=ran)


def adopt_line(rep: CandidateReport) -> str | None:
    """The ONLY place a clone line is allowed: skill/mod, verified exact repo, safety YES. Never run by us."""
    if rep.type in ADOPT_TYPES and rep.safety == "YES" and rep.owner_repo and not rep.name_only:
        return adopt_line_for(rep.url)
    return None


def _usage_lines(u: dict) -> str:
    bits = []
    bits.append(f"setup ~{u['setup_minutes']} min (estimate)" if "setup_minutes" in u else "setup time unknown")
    if "tokens" in u:
        lvl = f"{u['level']}, " if u.get("level") else ""
        bits.append(f"AI use {lvl}~{u['tokens']:,} tokens ({u['basis']})")
    else:
        bits.append("AI use " + (f"{u['level']} " if u.get("level") else "") + "(estimate, no token count)")
    bits.append(f"money: {u['money']} (estimate)" if u.get("money") else "money unknown (estimate)")
    return "; ".join(bits)


def format_report(rep: CandidateReport) -> tuple[str, str | None]:
    """(message text, clone line or None). Plain text only."""
    line = adopt_line(rep)
    ident = rep.owner_repo or "exact repo not confirmed"
    ident += " (matched from a name only)" if rep.name_only else ""
    typ = TYPES.get(rep.type, "unclear")
    lines = [f"{plain(rep.name, 80)} · type: {typ}"]
    if rep.type == "unclear":
        lines.append(f"Why unclear: {rep.type_note or 'I could not tell what kind of thing it is.'}")
    safe = f"1. Is it safe? {rep.safety}: {rep.safety_reason}"
    if rep.safety == "NO":
        safe += " I recommend Skip."
    lines += [safe, f"Repo: {ident}", "",
              f"2. What it does: {rep.what}" + (f" Problem it solves: {rep.problem}" if rep.problem else "")]
    if rep.pros:
        lines.append("Pros: " + "; ".join(rep.pros))
    if rep.cons:
        lines.append("Cons: " + "; ".join(rep.cons))
    lines += ["", f"3. Where it fits you: {rep.impact}", f"4. Usage: {_usage_lines(rep.usage)}"]
    if rep.examples:
        lines.append("Used for: " + "; ".join(rep.examples))
    if line:
        lines.append("Adopt shows the install command. I have NOT installed anything.")
    elif rep.type in ("app", "unclear"):
        lines.append("Read-only: no install button for this one. Nothing was installed.")
    else:
        lines.append("No install button: I'm not sure enough. Nothing was installed.")
    lines.append("I ran it in an isolated folder; nothing was installed." if rep.ran
                 else "I only read it; nothing was run or installed.")
    text = "\n".join(lines)
    return (text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"), line


def format_factcheck(verdict: Verdict, tail: str = "") -> str:
    """The short, separate fact-check message."""
    lines = [plain(verdict.summary, 400) or "No summary."]
    if verdict.claims:
        lines += ["", "Was the video telling the truth?"]
        for c in verdict.claims[:6]:
            lines.append(f"{_ICONS.get(c.verdict, '❔')} {plain(c.text, 140)} ({c.verdict})")
        lines.append("Sources are what the AI cited, not proof.")
    if tail:
        lines += ["", tail]
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def left_out_line(names: list[str]) -> str:
    shown = ", ".join(plain(n, 40) for n in names[:6]) + ("…" if len(names) > 6 else "")
    return f"Not reported ({len(names)}): {shown}. Send the video again to get them."
