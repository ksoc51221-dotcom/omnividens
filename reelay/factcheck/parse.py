"""Strict validation of the model's JSON answer. Anything odd is dropped or downgraded."""

import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

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


# Names that only exist on private networks. Checked by suffix because they often don't resolve.
_INTERNAL_SUFFIXES = (".internal", ".local", ".localhost", ".localdomain", ".lan", ".home", ".home.arpa",
                      ".arpa", ".corp", ".intranet", ".private", ".test", ".invalid", ".onion", ".example")
# What counts as a link in free text: a scheme, "www.", an email, a host followed by a path
# (any TLD), or a bare host with a TLD that is not also a common file extension. Bare names
# like install.sh, main.cc, next.ai or Node.js are left alone; they read as files, not links.
_HOST = r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*"
_URL_IN_TEXT = re.compile(
    r"(?:https?://|ftp://|www\.)\S+|\S+@\S+\.\S+|"
    + _HOST + r"\.[a-z]{2,24}/\S*|"
    + _HOST + r"\.(?:com|org|net|io|co|me|ly|xyz|info|gg|tv|gl|dev|app|link|page|site|online|ru|cn)\b",
    re.IGNORECASE)


def strip_urls(text: str) -> str:
    """Remove links from free model text; vetted URLs live only in sources/candidates."""
    return re.sub(r"\s+", " ", _URL_IN_TEXT.sub("[link removed]", text)).replace("]()", "]").strip()


def _addresses(host: str) -> list[str]:
    """IPs a name resolves to. Raises OSError if it doesn't resolve. Tests replace this."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def _is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def good_url(url, cache: dict | None = None) -> str | None:
    """An https URL to a public, resolvable host, else None. Fails closed when unsure."""
    if (not isinstance(url, str) or len(url) > 500 or "\\" in url
            or any(c.isspace() or ord(c) < 32 for c in url)):
        return None
    try:
        check_url(url)
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        port = urlparse(url).port
    except (FetchError, ValueError):
        return None
    tld = host.rsplit(".", 1)[-1]
    if "." not in host or not tld.isalpha() or host.endswith(_INTERNAL_SUFFIXES) or port not in (None, 443):
        return None  # needs a real name: no IPs, single-label or internal names, odd ports
    cache = cache if cache is not None else {}
    if host not in cache:
        try:
            addrs = _addresses(host)
            cache[host] = bool(addrs) and all(_is_public(a) for a in addrs)
        except (OSError, UnicodeError):
            cache[host] = False  # unresolvable: can't prove it's public
    return url if cache[host] else None


def extract_json(raw: str):
    """The first JSON object in the model's text that has a summary string.

    The model may add chatter or a code fence around it, even text with its own braces
    ("here {as asked}: {...}"), so try to decode an object at every "{".
    """
    decoder, pos = json.JSONDecoder(), raw.find("{")
    while pos >= 0:
        try:
            obj, _ = decoder.raw_decode(raw, pos)
        except (json.JSONDecodeError, RecursionError):
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("summary"), str) and obj["summary"].strip():
            return obj
        pos = raw.find("{", pos + 1)
    return None


def _claim(item, duration_s: float, cache: dict) -> Claim | None:
    if not isinstance(item, dict):
        return None
    text = strip_urls(_text(item.get("text"), 500))
    if not text:
        return None
    t = item.get("time_s")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= duration_s + 5:
        t = None
    sources = item.get("sources")
    urls = [u for u in (good_url(s, cache) for s in (sources if isinstance(sources, list) else [])[:MAX_SOURCES]) if u]
    verdict = item.get("verdict")
    verdict = verdict.strip().lower() if isinstance(verdict, str) else ""
    if verdict not in VERDICTS or (verdict != "unverified" and not urls):
        verdict = "unverified"  # unknown word, or a verdict nobody can check
    return Claim(text=text, time_s=float(t) if t is not None else None, verdict=verdict, sources=urls)


def _candidate(item, cache: dict) -> Candidate | None:
    if not isinstance(item, dict):
        return None
    name, url = _text(item.get("name"), 100), good_url(item.get("url"), cache)
    if not name or not url:
        return None
    kind = item.get("kind")
    return Candidate(name=name, kind=kind if kind in KINDS else "other", url=url)


def parse_verdict(raw: str, duration_s: float = 0.0) -> Verdict:
    data = extract_json(raw) if isinstance(raw, str) else None
    if not isinstance(data, dict):
        return failed("the answer was not valid JSON")
    cache: dict = {}
    summary = strip_urls(_text(data.get("summary"), 2000))
    if not summary:
        return failed("the answer had no summary")
    claims_in = data.get("claims") if isinstance(data.get("claims"), list) else []
    claims = [c for c in (_claim(i, duration_s, cache) for i in claims_in[:MAX_CLAIMS]) if c]
    cands, seen = [], set()
    for item in (data.get("candidates") if isinstance(data.get("candidates"), list) else [])[:MAX_CANDIDATES]:
        c = _candidate(item, cache)
        if c and c.url not in seen:
            seen.add(c.url)
            cands.append(c)
    return Verdict(summary=summary, claims=claims, candidates=cands)
