"""report: verdict.json -> one validated plain-text report -> relay."""

import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from reelay.cloud.job import github_links, parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.core import reports as _reports
from reelay.sandbox.fetch import MAX_CLONE_BYTES, FetchError, check_url
from reelay.sandbox.fetch import _size as fetch_size
from reelay.core.types import Frame, Profile, Verdict, VideoBundle
from reelay.factcheck.parse import extract_json, extract_reports_json, parse_verdict
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


def _label_key(url) -> str:
    p = _reports.repo_parts(url)
    return (f"{p[0]}/{p[1]}" if p else str(url)).lower()


def saw_line(viewed, total: int) -> str:
    """What the routine says it looked at, never more than what was extracted."""
    if total <= 0:
        return "No frames were extracted."
    if isinstance(viewed, float) and viewed.is_integer():
        viewed = int(viewed)
    if isinstance(viewed, bool) or not isinstance(viewed, int):
        return f"{total} frames extracted, number viewed not reported."
    return f"Saw: {max(0, min(viewed, total))} of {total} frames."


def _build(data: dict, verdict, projects: list[str]):
    """(reports, left-out labels) in video order, at most MAX_REPORTS, one per URL."""
    reps, seen, left = [], set(), []
    items = data.get("reports") if isinstance(data.get("reports"), list) else []
    for item in items:
        r = _reports.parse_report(item, projects)
        key = (r.owner_repo or r.url).lower() if r else None
        if r is None or key in seen:
            continue
        seen.add(key)
        if len(reps) < _reports.MAX_REPORTS:
            reps.append(r)
        else:
            left.append(_reports.repo_label(r.url, r.name))
    left += [_reports.repo_label(c.url, c.name) for c in verdict.candidates
             if (_label_key(c.url)) not in seen]
    for u in (data.get("left_out") if isinstance(data.get("left_out"), list) else [])[:30]:
        p = _reports.repo_parts(u)
        if p and f"{p[0]}/{p[1]}".lower() not in seen and f"{p[0]}/{p[1]}" not in left:
            left.append(f"{p[0]}/{p[1]}")
    return reps, left


def _confirmed(bundle_d: dict, bundle: VideoBundle, supplied: list[str]) -> set[str]:
    """owner/repo values the video or the owner really gave us. On-screen text is not recorded, so it
    does not count."""
    found = list(bundle_d.get("github_links") or []) + github_links(
        bundle.caption, bundle.transcript, str(bundle_d.get("message_text") or ""))
    out = {u.split("github.com/", 1)[1].lower() for u in found if isinstance(u, str) and "github.com/" in u}
    return out | {s.lower() for s in supplied}


def _dir_bytes(path: Path) -> int:
    """Size of a folder git is still writing into; files vanish mid-scan, so errors count as 0."""
    try:
        return fetch_size(path) if path.exists() else 0
    except OSError:
        return 0


def _hardened_clone(url: str, dest: Path, timeout_s: int = 60) -> str | None:
    """Our own shallow clone for the gate (never the model's). Same limits as sandbox/fetch.py, plus LFS off.
    Returns the commit scanned, or raises."""
    check_url(url)
    cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
           "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
           "clone", "--depth", "1", "--no-tags", "--single-branch", "--", url, str(dest)]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_TERMINAL_PROMPT": "0",
           "GIT_LFS_SKIP_SMUDGE": "1"}
    # own process group, so a timeout or an oversized clone kills git and anything it started
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                            start_new_session=True)
    deadline = time.monotonic() + timeout_s
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline or _dir_bytes(dest) > MAX_CLONE_BYTES:
                raise FetchError("git clone timed out or is over 50 MB")
            time.sleep(0.25)
    except BaseException:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    if proc.returncode != 0:
        raise FetchError("git clone failed")
    if fetch_size(dest) > MAX_CLONE_BYTES:
        raise FetchError("repository is larger than 50 MB")
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True,
                          timeout=20, env=env)
    commit = head.stdout.strip()
    return commit if head.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) else None


def run(workdir: str, relay_factory=Relay, cloner=_hardened_clone) -> int:
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
        except (OSError, ValueError):
            bundle_d = None
        try:  # read apart from the bundle: a missing verdict must not discard the frames
            raw = (work / "verdict.json").read_text(encoding="utf-8")
        except (OSError, ValueError):
            raw = ""
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
        supplied = [x for x in (bundle_d.get("owner_supplied") or []) if isinstance(x, str)][:1]
        repo_only = bool(supplied)  # /report owner/repo: one repo, no fact-check message
        vstats: dict = {}
        # Cloud: claim sources are only displayed, never fetched, so they get a shape check without DNS
        # (cited by the model, not resolved). Candidates are still fully checked inside parse_verdict.
        verdict = (parse_verdict(raw, bundle.duration_s, resolve_sources=False, stats=vstats)
                   if not repo_only else Verdict(summary="", claims=[]))
        # Prefer the object holding `reports`: a /report job writes no summary, and a video whose
        # summary went missing should still keep its reports.
        data = (extract_reports_json(raw) or extract_json(raw)) if raw else None
        data = data if isinstance(data, dict) else {}
        reps, left = _build(data, verdict, _projects(prof_d.get("projects")))
        if repo_only:
            reps = [r for r in reps if r.owner_repo and r.owner_repo.lower() == supplied[0].lower()][:1]
            left = []
        confirmed = _confirmed(bundle_d, bundle, supplied)
        with tempfile.TemporaryDirectory(prefix="reelay-gate-") as tmp:  # always removed
            for i, r in enumerate(reps):
                clone, commit = Path(tmp) / f"c{i}", None
                try:
                    if _reports.repo_parts(r.url):
                        commit = cloner(r.url, clone)
                except Exception as e:  # noqa: BLE001 - a failed clone means UNSURE, never a pass
                    print(f"reelay: clone failed ({type(e).__name__})")
                    shutil.rmtree(clone, ignore_errors=True)
                    clone = None
                _reports.apply_gate(r, clone, confirmed, commit)
        sent, daily = 0, False
        if not repo_only:
            relay.send(job.chat_id, _reports.format_factcheck(
                verdict, "" if reps or left else "Done. Nothing to install in this one.",
                saw=saw_line(data.get("frames_viewed"), len(frames))))
            sent += 1
        elif not reps:
            relay.send(job.chat_id, "I couldn't build a report for that repo. Nothing was installed.")
            sent += 1
        done = 0
        for i, rep in enumerate(reps):
            text, line = _reports.format_report(rep)
            try:
                relay.send(job.chat_id, text, line, report=True, adopt_note=_reports.adopt_note(rep))
                sent, done = sent + 1, done + 1
            except RelayError as e:  # daily budget or relay trouble: stop and say what was left out
                daily = getattr(e, "status", None) == 429
                left = [_reports.repo_label(r.url, r.name) for r in reps[i:]] + left
                break
        if left:
            relay.send(job.chat_id, _reports.left_out_line(left, daily))
            sent += 1
        started = bundle_d.get("started_at")
        secs = f"{time.time() - started:.0f}" if isinstance(started, (int, float)) else "unknown"
        print(f"reelay: claims={len(verdict.claims)} sources_kept={vstats.get('sources_kept', 0)} "
              f"sources_dropped={vstats.get('sources_dropped', 0)}")
        print(f"reelay: run candidates={len(reps) + len(left)} reports={done} messages={sent} seconds={secs}")
        try:
            prof_d.setdefault("traits", {})
            if isinstance(prof_d["traits"], dict):
                prof_d["traits"]["last_checked"] = datetime.date.today().isoformat()
                traits = {**profile.traits, "last_checked": prof_d["traits"]["last_checked"]}
                finds = len(reps) + len(left)
                if isinstance(started, (int, float)) and finds > 0 and 0 < time.time() - started < 6 * 3600:
                    # measured seconds per find: the next "About N min" estimate uses it (summary.py)
                    traits["sec_per_find"] = str(max(1, round((time.time() - started) / finds)))
                relay.put_profile({"vibe": profile.vibe, "goals": profile.goals, "traits": traits})
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
