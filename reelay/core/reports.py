"""reports: one short fact-check message plus one validated report per candidate (cloud and local share it).

Everything the routine hands us was produced after reading untrusted video and repo text, so each field
is validated here: unknown or missing values fail closed to read-only text with no `adopt_line`.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from reelay.core.types import Verdict
from reelay.sandbox.safety import find_problem

TYPES = {"skill": "bot skill", "mod": "mod", "app": "full app"}  # fixed list; anything else is "unclear"
ADOPT_TYPES = {"skill", "mod"}
# A mod is code that runs on the user's machine, so a clean scan of a repo that contains code can never be
# "YES". Owner decision pending: True keeps Adopt for a skill/mod with confirmed identity and file evidence
# even when it contains code (UNSURE + warning); False makes that case read-only.
ADOPT_ALLOWED_WITH_CODE = True
VERDICTS = ("YES", "NO", "UNSURE")
MAX_REPORTS = 9  # per video
MAX_TEXT = 3800
NO_PROJECTS = "No project list yet. Send /projects to add one."
NO_FIT = "No fit with your listed projects."
_REPO = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)")
# control chars, plus bidi overrides/isolates and zero-width marks that can hide or reorder text
_CTRL = re.compile("[\x00-\x1f\x7f\u061c\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]+")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_SCRIPT_EXT = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd",
               ".command", ".php"}
_MANIFESTS = {"plugin.json", "settings.json", "settings.local.json", "hooks.json", "package.json"}
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
    if not m or {m.group(1), m.group(2)} & {".", ".."} or m.group(2).lower().endswith(".git"):
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
    ran: bool = False  # the routine never runs anything; always False
    code_paths: list[str] = field(default_factory=list)  # executable-looking files found in the clone (max 5)
    has_code: bool = False
    commit: str | None = None  # the commit our own clone scanned
    model_safety: str = "UNSURE"  # what the model said; it can only lower the final verdict
    claim_matches: bool = False


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
        out["basis"] = "estimate"  # nothing in the routine measures real token use yet, so never "measured"
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
    owner_repo = f"{parts[0]}/{parts[1]}" if parts else None
    claim_matches = bool(owner_repo) and claimed == owner_repo.lower()
    safety_in = raw.get("safety") if isinstance(raw.get("safety"), dict) else {}
    model_safety = str(safety_in.get("verdict", "")).upper()
    model_safety = model_safety if model_safety in VERDICTS else "UNSURE"
    # The model's words never raise safety, identity or type: apply_gate() decides from real evidence.
    safety, reason = "UNSURE", plain(safety_in.get("reason"), 200)
    info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
    return CandidateReport(
        name=name, url=url, type=typ, type_note=plain(raw.get("type_note"), 200), safety=safety,
        safety_reason=reason, owner_repo=owner_repo, name_only=True, model_safety=model_safety,
        claim_matches=claim_matches,
        what=plain(info.get("what"), 400) or "No description was found.", problem=plain(info.get("problem"), 200),
        pros=_strs(info.get("pros"), 2, 160), cons=_strs(info.get("cons"), 2, 160),
        impact=_impact(raw.get("impact"), projects), usage=_usage(raw.get("usage")),
        examples=_strs(info.get("examples"), 3, 160))


def type_evidence(clone: Path) -> set[str]:
    """Types the files in the clone really support. Never reads file contents."""
    ev: set[str] = set()
    try:
        if (clone / "SKILL.md").is_file() or any(clone.glob("skills/*/SKILL.md")) \
                or any(clone.glob(".claude/skills/*/SKILL.md")):
            ev.add("skill")
        if (clone / ".claude-plugin" / "plugin.json").is_file() or (clone / "hooks").is_dir() \
                or (clone / "commands").is_dir():
            ev.add("mod")
    except OSError:
        pass
    return ev


def _safe_dir(clone: Path | None) -> bool:
    return clone is not None and clone.is_dir() and not clone.is_symlink()


def _clean_path(rel: str) -> str:
    """A repo path made safe to show: plain characters only."""
    return re.sub(r"[^A-Za-z0-9._/@+-]", "?", rel)[:80]


def find_code(clone: Path) -> list[str]:
    """Executable-looking content: hooks/, scripts/, script files, or a manifest that mentions hooks.
    Returns sanitised relative paths (the first 5 are shown); empty means none was found."""
    found: list[str] = []
    seen = 0
    try:
        for p in sorted(clone.rglob("*")):
            rel = p.relative_to(clone)
            if ".git" in rel.parts:
                continue
            seen += 1
            if seen > 5000:
                break
            low = [x.lower() for x in rel.parts]
            hit = False
            if p.is_dir() and low[-1] in ("hooks", "scripts") and len(low) <= 4:
                hit = True
            elif p.is_file():
                if p.suffix.lower() in _SCRIPT_EXT or any(x in ("hooks", "scripts") for x in low[:-1]):
                    hit = True
                elif low[-1] in _MANIFESTS and p.stat().st_size < 1_000_000 \
                        and b'"hooks"' in p.read_bytes()[:1_000_000].lower():
                    hit = True
            if hit:
                found.append(_clean_path(rel.as_posix()))
    except OSError:
        pass
    return found


def apply_gate(rep: CandidateReport, clone: Path | None, confirmed: set[str],
               commit: str | None = None) -> CandidateReport:
    """Decide safety, identity and type from evidence, not from model output.

    `clone` is OUR OWN shallow clone (never the model's); `confirmed` is the lowercase owner/repo set the
    owner or the video itself supplied (links in the caption, transcript or message, or typed in /report)."""
    rep.ran = False
    rep.commit = commit if isinstance(commit, str) and _COMMIT.fullmatch(commit) else None
    ok_id = bool(rep.owner_repo) and rep.claim_matches and rep.owner_repo.lower() in confirmed
    rep.name_only = not ok_id
    rep.has_code, rep.code_paths = False, []
    # safety: our own scan of the clone
    scan = "unknown"
    why = "I couldn't fetch it to scan it, so I only know what the README claims."
    if _safe_dir(clone):
        try:
            problem = find_problem(clone)
            code = find_code(clone) if not problem else []
        except Exception:  # noqa: BLE001 - a scan error is never a pass
            why = "The safety scan could not finish."
        else:
            if problem and str(problem).startswith("too large"):
                why = "It is too big to scan, so I only know what the README claims."
            elif problem:
                scan, why = "high", f"The safety scan flagged it: {plain(problem, 140)}."
            elif code:
                scan, rep.has_code, rep.code_paths = "code", True, code[:5]
                why = ("It contains code that would run on your computer. I did not read or run it; read "
                       "these first: " + ", ".join(rep.code_paths))
            else:
                scan, why = "clean", ("I found none of the patterns I check for (hooks in skill files, "
                                      "symlinks, shell commands on load). That is not a guarantee.")
    if scan == "high" or rep.model_safety == "NO":
        rep.safety = "NO"
        rep.safety_reason = why if scan == "high" else (rep.safety_reason or "The review found risky behaviour.")
    elif scan == "clean" and rep.model_safety == "YES" and ok_id:
        rep.safety, rep.safety_reason = "YES", why
    else:
        rep.safety = "UNSURE"
        if scan in ("clean", "code") and not ok_id:
            rep.safety_reason = "I couldn't confirm this is the exact repo the video meant."
        elif scan == "clean":
            rep.safety_reason = rep.safety_reason or "The scan found nothing, but the review was not sure. I only read the code, I didn't run it."
        else:
            rep.safety_reason = why  # fixed, written by code
    # type: a claimed skill or mod must be backed by files in the clone
    if rep.type in ADOPT_TYPES:
        ev = type_evidence(clone) if _safe_dir(clone) else set()
        if rep.type not in ev:
            need = "SKILL.md" if rep.type == "skill" else "plugin manifest, hooks/ or commands/"
            rep.type_note = f"It says it is a {TYPES[rep.type]}, but I found no {need} in its files."
            rep.type = "unclear"
    return rep


def adopt_line(rep: CandidateReport) -> str | None:
    """The ONLY place a clone line is allowed. Never run by us.

    Needs a skill/mod backed by files, an exact repo confirmed by the video or the owner, and either safety
    YES or (while ADOPT_ALLOWED_WITH_CODE) the one UNSURE case: a clean scan of a repo that contains code."""
    if rep.type not in ADOPT_TYPES or not rep.owner_repo or rep.name_only:
        return None
    if rep.safety == "YES" and not rep.has_code:
        return adopt_line_for(rep.url)
    if rep.safety == "UNSURE" and rep.has_code and ADOPT_ALLOWED_WITH_CODE:
        return adopt_line_for(rep.url)
    return None


def adopt_note(rep: CandidateReport) -> str | None:
    """Plain text the relay prints above the clone line when Adopt is tapped (fixed wording, code-written)."""
    if not adopt_line(rep):
        return None
    parts = []
    if rep.has_code:
        parts.append("⚠️ It contains code that would run on your computer. Read these first: "
                     + ", ".join(rep.code_paths))
    if rep.commit:
        parts.append(f"I scanned commit {rep.commit[:12]}. The repo may have changed since, so check the commit "
                     "you get.")
    parts.append("I have NOT installed anything.")
    return "\n".join(parts)


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
    if rep.commit:
        ident += f" · scanned commit {rep.commit[:12]}"
    lines += [safe, f"Repo: {ident}", "",
              f"2. What it does: {rep.what}" + (f" Problem it solves: {rep.problem}" if rep.problem else "")]
    if rep.pros:
        lines.append("Pros: " + "; ".join(rep.pros))
    if rep.cons:
        lines.append("Cons: " + "; ".join(rep.cons))
    lines += ["", f"3. Where it fits you: {rep.impact}", f"4. Usage: {_usage_lines(rep.usage)}"]
    if rep.examples:
        lines.append("Used for: " + "; ".join(rep.examples))
    if line and rep.has_code:
        lines.append("⚠️ It contains code that runs on your computer. Adopt only shows the install command. "
                     "I have NOT installed anything.")
    elif line:
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


def repo_label(url, name) -> str:
    p = repo_parts(url)
    return f"{p[0]}/{p[1]}" if p else plain(name, 40)


def left_out_line(labels: list[str], daily: bool = False) -> str:
    shown = ", ".join(plain(n, 60) for n in labels[:6]) + ("…" if len(labels) > 6 else "")
    why = "I hit today's report limit. " if daily else ""
    return (f"{why}Not reported ({len(labels)}): {shown}. Send /report owner/repo to get one "
            "(each one starts a new run and uses your Claude usage).")
