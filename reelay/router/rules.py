"""Cheap rule-based parsing of obvious flags (skill, model, effort, project). No network."""

import re
from dataclasses import dataclass
from pathlib import Path

MODELS = {"opus", "sonnet", "haiku", "fable"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SKILL_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")  # never starts with "-": it ends up in a CLI call

_SKILL_A = re.compile(r"\b(?:use|run|with|apply)\s+(?:the\s+)?([\w.-]+)\s+skill\b", re.I)
_SKILL_B = re.compile(r"\bskill\s*[:=]?\s+([\w.-]+)", re.I)
_MODEL = re.compile(r"\b(opus|sonnet|haiku|fable)\b", re.I)
_EFFORT_A = re.compile(r"\beffort\s*[:=]?\s*(low|medium|high|xhigh|max)\b", re.I)
_EFFORT_B = re.compile(r"\b(low|medium|high|xhigh|max)\s+effort\b", re.I)
_PROJECT = re.compile(r"(?:^|\s)((?:~|\.{1,2})?/[\w./~-]+)")


@dataclass
class Parsed:
    """What the rules found, plus the text left over once the flags are removed."""
    task: str
    skill: str | None = None
    model: str | None = None
    effort: str | None = None
    project: Path | None = None

    @property
    def has_flags(self) -> bool:
        return any((self.skill, self.model, self.effort, self.project))


def _take(pattern: re.Pattern, text: str) -> tuple[str | None, str]:
    """Return the first capture group (lowercased pattern hit) and the text with the match removed."""
    match = pattern.search(text)
    if not match:
        return None, text
    return match.group(1), text[: match.start()] + " " + text[match.end():]


def _tidy(text: str) -> str:
    text = re.sub(r"\s*,\s*(?=,|$)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;.:-")


def parse(text: str) -> Parsed:
    """Pull out obvious flags. Anything unusual is left for Haiku."""
    rest = text
    skill, rest = _take(_SKILL_A, rest)
    if skill is None:
        skill, rest = _take(_SKILL_B, rest)
    if skill is not None and not SKILL_NAME.match(skill):
        skill = None
    effort, rest = _take(_EFFORT_A, rest)
    if effort is None:
        effort, rest = _take(_EFFORT_B, rest)
    model, rest = _take(_MODEL, rest)
    project, rest = _take(_PROJECT, rest)
    return Parsed(
        task=_tidy(rest),
        skill=skill,
        model=model.lower() if model else None,
        effort=effort.lower() if effort else None,
        project=Path(project).expanduser() if project else None,
    )
