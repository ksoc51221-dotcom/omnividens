"""Strict validation of the model's JSON answer. Anything odd is dropped or downgraded."""

import json
import re

from reelay.core.types import Candidate, Claim, Verdict
from reelay.sandbox.fetch import FetchError, check_url

VERDICTS = {"true", "false", "misleading", "unverified"}
KINDS = {"skill", "tool", "repo", "other"}
MAX_CLAIMS, MAX_CANDIDATES, MAX_SOURCES = 10, 5, 5
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def failed(reason: str) -> Verdict:
    """The safe result when checking didn't work. Honest, no claims, no candidates."""
    return Verdict(summary=f"I couldn't fact-check this video automatically ({reason}).", claims=[])


def _text(value, limit: int) -> str:
    return _CTRL.sub("", value).strip()[:limit] if isinstance(value, str) else ""


def good_url(url) -> str | None:
    """An https URL to a public host, else None."""
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        check_url(url)
    except FetchError:
        return None
    return url


def extract_json(raw: str):
    """Find the JSON object in the model's text (it may be wrapped in a code fence)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, RecursionError):
        return None


def _claim(item, duration_s: float) -> Claim | None:
    if not isinstance(item, dict):
        return None
    text = _text(item.get("text"), 500)
    if not text:
        return None
    t = item.get("time_s")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= duration_s + 5:
        t = None
    sources = item.get("sources")
    urls = [u for u in (good_url(s) for s in (sources if isinstance(sources, list) else [])[:MAX_SOURCES]) if u]
    verdict = item.get("verdict")
    verdict = verdict.strip().lower() if isinstance(verdict, str) else ""
    if verdict not in VERDICTS or (verdict != "unverified" and not urls):
        verdict = "unverified"  # unknown word, or a verdict nobody can check
    return Claim(text=text, time_s=float(t) if t is not None else None, verdict=verdict, sources=urls)


def _candidate(item) -> Candidate | None:
    if not isinstance(item, dict):
        return None
    name, url = _text(item.get("name"), 100), good_url(item.get("url"))
    if not name or not url:
        return None
    kind = item.get("kind")
    return Candidate(name=name, kind=kind if kind in KINDS else "other", url=url)


def parse_verdict(raw: str, duration_s: float = 0.0) -> Verdict:
    data = extract_json(raw) if isinstance(raw, str) else None
    if not isinstance(data, dict):
        return failed("the answer was not valid JSON")
    summary = _text(data.get("summary"), 2000)
    if not summary:
        return failed("the answer had no summary")
    claims_in = data.get("claims") if isinstance(data.get("claims"), list) else []
    claims = [c for c in (_claim(i, duration_s) for i in claims_in[:MAX_CLAIMS]) if c]
    cands, seen = [], set()
    for item in (data.get("candidates") if isinstance(data.get("candidates"), list) else [])[:MAX_CANDIDATES]:
        c = _candidate(item)
        if c and c.url not in seen:
            seen.add(c.url)
            cands.append(c)
    return Verdict(summary=summary, claims=claims, candidates=cands)
