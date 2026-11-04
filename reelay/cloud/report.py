"""report: verdict.json -> one validated plain-text report -> relay."""

import datetime
import json
import re
from pathlib import Path

from reelay.cloud.job import parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.core.pipeline import clone_command, format_trial, format_verdict, one_line
from reelay.core.types import Candidate, Frame, Profile, TrialReport, VideoBundle
from reelay.factcheck.parse import extract_json, parse_verdict
from reelay.profile.store import MAX_GOAL_LEN, MAX_GOALS, MAX_TRAIT_LEN, MAX_TRAITS, VIBES

_REPO = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)")
_SCANS = {"clean", "review", "high"}
MAX_REPORT = 3800


def adopt_line_for(urls) -> str | None:
    """`git clone -- https://github.com/o/r` for the first plain GitHub repo URL, else None."""
    for u in urls:
        m = _REPO.fullmatch(u) if isinstance(u, str) else None
        if m and m.group(1) not in (".", "..") and m.group(2) not in (".", "..") and not m.group(2).endswith(".git"):
            return f"git clone -- {u}"
    return None


def _profile(d: dict, chat_id) -> Profile:
    vibe = d.get("vibe")
    goals = d.get("goals") if isinstance(d.get("goals"), list) else []
    traits = d.get("traits") if isinstance(d.get("traits"), dict) else {}
    return Profile(user_id=str(chat_id), vibe=vibe if vibe in VIBES else "balanced",
                   goals=[str(g)[:MAX_GOAL_LEN] for g in goals[:MAX_GOALS]],
                   traits={str(k)[:MAX_TRAIT_LEN]: str(v)[:MAX_TRAIT_LEN] for k, v in list(traits.items())[:MAX_TRAITS]})


def _trial(raw, candidates: list[Candidate], profile: Profile) -> tuple[str, Candidate] | None:
    """Optional `trial` object in verdict.json. The repo must be one of the validated candidates."""
    if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
        return None
    cand = next((c for c in candidates if c.url == raw["url"]), None)
    if cand is None:
        return None
    scan = raw.get("scan_verdict") if raw.get("scan_verdict") in _SCANS else "review"
    secs = raw.get("seconds")
    secs = float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) and 0 <= secs < 86400 else 0.0
    fit = raw.get("fit") if isinstance(raw.get("fit"), str) else ""
    rep = TrialReport(candidate=cand, installed=bool(raw.get("fetched", True)), ran=raw.get("ran") is True,
                      scan_verdict=scan, tokens_used=0, seconds=secs,
                      fit=one_line(fit, 300) or "Scan-only: no fit note.")
    return format_trial(rep, profile), cand


def run(workdir: str, relay_factory=Relay) -> int:
    work = Path(workdir)
    try:
        job = parse_job((work / "job.json").read_text(encoding="utf-8"))
    except (OSError, RelayError) as e:
        print(f"reelay: no valid job in {work} ({type(e).__name__})")
        return 2
    relay = relay_factory(job.relay_url)
    try:
        try:
            bundle_d = json.loads((work / "bundle.json").read_text(encoding="utf-8"))
            raw = (work / "verdict.json").read_text(encoding="utf-8")
        except (OSError, ValueError):
            bundle_d, raw = None, ""
        if not isinstance(bundle_d, dict):
            bundle_d = {}
        frames = [Frame(float(f.get("time_s") or 0), Path(str(f.get("path"))))
                  for f in bundle_d.get("frames", []) if isinstance(f, dict)]
        duration = bundle_d.get("duration_s") if isinstance(bundle_d.get("duration_s"), (int, float)) else 0.0
        bundle = VideoBundle(source_url=None, frames=frames, transcript=str(bundle_d.get("transcript") or ""),
                             caption=str(bundle_d.get("caption") or ""), duration_s=float(duration))
        try:
            prof_d = json.loads((work / "profile.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prof_d = {}
        prof_d = prof_d if isinstance(prof_d, dict) else {}
        profile = _profile(prof_d, job.chat_id)
        verdict = parse_verdict(raw, bundle.duration_s)
        parts = [format_verdict(verdict, profile, bundle)]
        urls = [c.url for c in verdict.candidates]
        trial_raw = (extract_json(raw) or {}).get("trial") if raw else None
        t = _trial(trial_raw, verdict.candidates, profile)
        if t:
            parts.append(t[0])
            if t[1].url in urls:  # the tried repo gets the Adopt button
                urls.insert(0, urls.pop(urls.index(t[1].url)))
        text = "\n\n".join(parts)
        if len(text) > MAX_REPORT:
            text = text[:MAX_REPORT - 1] + "…"
        adopt = adopt_line_for(urls)
        relay.send(job.chat_id, text, adopt)
        try:
            prof_d.setdefault("traits", {})
            if isinstance(prof_d["traits"], dict):
                prof_d["traits"]["last_checked"] = datetime.date.today().isoformat()
                relay.put_profile({"vibe": profile.vibe, "goals": profile.goals, "traits": {**profile.traits, "last_checked": prof_d["traits"]["last_checked"]}})
        except Exception:  # noqa: BLE001 - profile update is best effort
            pass
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"reelay: report failed ({type(e).__name__})")
        try:
            relay.send(job.chat_id, "Sorry, I couldn't build the report for that video.")
        except Exception:  # noqa: BLE001
            pass
        return 2
