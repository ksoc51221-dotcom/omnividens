"""report: verdict.json -> one validated plain-text report -> relay."""

import datetime
import json
from pathlib import Path

from reelay.cloud.job import parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.core import reports as _reports
from reelay.core.types import Frame, Profile, VideoBundle
from reelay.factcheck.parse import extract_json, parse_verdict
from reelay.profile.store import MAX_GOAL_LEN, MAX_GOALS, MAX_TRAIT_LEN, MAX_TRAITS, VIBES

MAX_REPORT = 3800
adopt_line_for = _reports.adopt_line_for  # kept for callers: strict github.com/owner/repo only


def _profile(d: dict, chat_id) -> Profile:
    vibe = d.get("vibe")
    goals = d.get("goals") if isinstance(d.get("goals"), list) else []
    traits = d.get("traits") if isinstance(d.get("traits"), dict) else {}
    return Profile(user_id=str(chat_id), vibe=vibe if vibe in VIBES else "balanced",
                   goals=[str(g)[:MAX_GOAL_LEN] for g in goals[:MAX_GOALS]],
                   traits={str(k)[:MAX_TRAIT_LEN]: str(v)[:MAX_TRAIT_LEN] for k, v in list(traits.items())[:MAX_TRAITS]})


def _projects(raw) -> list[str]:
    """Project names from the owner's written list ("Name: one line"). Matching data only."""
    out = []
    for line in (raw if isinstance(raw, list) else [])[:20]:
        name = _reports.plain(str(line).split(":", 1)[0], 80)
        if name:
            out.append(name)
    return out


def _build(data: dict, verdict, projects: list[str]):
    """(reports, left-out names) in video order, at most MAX_REPORTS, one per URL."""
    reps, seen, left = [], set(), []
    items = data.get("reports") if isinstance(data.get("reports"), list) else []
    for item in items:
        r = _reports.parse_report(item, projects)
        if r is None or r.url in seen:
            continue
        seen.add(r.url)
        if len(reps) < _reports.MAX_REPORTS:
            reps.append(r)
        else:
            left.append(r.name)
    left += [c.name for c in verdict.candidates if c.url not in seen]
    return reps, left


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
        data = extract_json(raw) if raw else None
        reps, left = _build(data if isinstance(data, dict) else {}, verdict, _projects(prof_d.get("projects")))
        tail = "" if reps else "Done. Nothing to install in this one."
        relay.send(job.chat_id, _reports.format_factcheck(verdict, tail))
        for i, rep in enumerate(reps):
            text, line = _reports.format_report(rep)
            try:
                relay.send(job.chat_id, text, line, report=True)
            except RelayError:  # daily budget or relay trouble: stop and say what was left out
                left = [r.name for r in reps[i:]] + left
                break
        if left:
            relay.send(job.chat_id, _reports.left_out_line(left))
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
            relay.send(job.chat_id, "Something broke. Nothing was installed. Try again.")
        except Exception:  # noqa: BLE001
            pass
        return 2
