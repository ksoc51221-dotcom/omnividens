"""Parse and validate the JOB JSON the relay fires the routine with. Everything in it is data."""

import json
import re
from dataclasses import dataclass

from reelay.cloud.relay import RelayError, check_relay_url

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_GH_LINK = re.compile(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", re.IGNORECASE)
_GH_BARE = re.compile(r"\b(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)", re.IGNORECASE)


@dataclass
class Job:
    chat_id: int
    message_id: int
    update_id: int
    text: str
    file_id: str | None
    relay_url: str
    raw: dict


def _int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RelayError("bad job")
    return v


def parse_job(text: str) -> Job:
    """Raises RelayError on anything malformed (the message never contains job text)."""
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        raise RelayError("the job was not valid JSON") from None
    if not isinstance(d, dict) or d.get("v") != 1:
        raise RelayError("unsupported job version")
    relay_url = check_relay_url(d.get("relay_url"))
    f = d.get("file")
    file_id = f.get("file_id") if isinstance(f, dict) else None
    if file_id is not None and (not isinstance(file_id, str) or not 0 < len(file_id) <= 300):
        raise RelayError("bad file id")
    msg = d.get("text")
    return Job(chat_id=_int(d.get("chat_id")), message_id=_int(d.get("message_id", 0)),
               update_id=_int(d.get("update_id", 0)), text=msg[:4000] if isinstance(msg, str) else "",
               file_id=file_id, relay_url=relay_url, raw=d)


def first_url(text: str) -> str | None:
    m = _URL.search(text or "")
    return m.group(0).rstrip(".,;:!?") if m else None


def github_links(*texts: str) -> list[str]:
    """Distinct https://github.com/owner/repo links found in free text (unvalidated hints)."""
    out: list[str] = []
    for t in texts:
        t = t or ""
        found = [m.group(0) for m in _GH_LINK.finditer(t)]
        found += [f"https://github.com/{m.group(1)}/{m.group(2)}" for m in _GH_BARE.finditer(t)]
        for u in found:
            u = u.rstrip(".")
            if u.lower().endswith(".git"):
                u = u[:-4]
            if u not in out:
                out.append(u)
    return out[:10]
