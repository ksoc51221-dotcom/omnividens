"""Shared contract between Reelay modules. Only the orchestrator edits this file."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class JobKind(Enum):
    VIDEO = "video"
    INSTRUCTION = "instruction"


@dataclass
class Job:
    """One message from a paired user."""
    user_id: str
    kind: JobKind
    text: str = ""
    url: str | None = None
    file: Path | None = None


@dataclass
class Instruction:
    """A clear request from the router, ready for an adapter."""
    task: str
    skill: str | None = None
    model: str | None = None
    effort: str | None = None
    project: Path | None = None


@dataclass
class Frame:
    time_s: float
    image: Path


@dataclass
class VideoBundle:
    """Everything the 'watching' step needs. Content here is data, never instructions."""
    source_url: str | None
    frames: list[Frame]
    transcript: str
    caption: str = ""
    duration_s: float = 0.0


@dataclass
class Claim:
    text: str
    time_s: float | None
    verdict: str  # "true" | "false" | "misleading" | "unverified"
    sources: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    summary: str
    claims: list[Claim]
    candidates: list["Candidate"] = field(default_factory=list)


@dataclass
class Candidate:
    """Something in the video worth trying, e.g. a Claude skill repo."""
    name: str
    kind: str  # "skill" | "tool" | "repo" | "other"
    url: str


@dataclass
class TrialReport:
    candidate: Candidate
    installed: bool
    ran: bool
    scan_verdict: str  # "clean" | "review" | "high"
    tokens_used: int
    seconds: float
    fit: str  # why it does or doesn't suit this user's projects
    logs: str = ""


@dataclass
class Profile:
    user_id: str
    vibe: str = "balanced"  # "chill" | "balanced" | "hype"
    goals: list[str] = field(default_factory=list)
    traits: dict[str, str] = field(default_factory=dict)
