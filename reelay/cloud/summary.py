"""summary: finds.json -> ONE short "here is what I found" message, sent before cloning or scanning.

`python -m reelay.cloud summary <workdir>`. The routine writes `<workdir>/finds.json`:
`{"finds": [{"name": "...", "what": "1-2 sentences"}]}` (up to 9). Everything in it is data derived from
untrusted text: it is capped and stripped to plain text, and the message has no buttons or clone lines.
No finds: nothing is sent. A failure here must never stop the run, so this always exits 0 after a job loads.
"""

import json
import math
from pathlib import Path

from reelay.cloud.job import parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.core.reports import MAX_REPORTS, plain, plain_sentence

DEFAULT_SEC_PER_FIND = 90  # a first guess, not measured; replaced by the last measured run (see report.py)
MIN_MINUTES = 2
MAX_SEC_PER_FIND = 600


def sec_per_find(profile: dict) -> float:
    """Last measured seconds per find from the stored profile, or the default. Never raises."""
    try:
        v = float(str((profile.get("traits") or {}).get("sec_per_find")))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_SEC_PER_FIND
    return v if math.isfinite(v) and 0 < v <= MAX_SEC_PER_FIND else DEFAULT_SEC_PER_FIND


def minutes(profile: dict, n: int) -> int:
    return max(MIN_MINUTES, math.ceil(sec_per_find(profile) * max(n, 1) / 60))


def build(finds, profile: dict) -> str | None:
    """The message text, or None when there is nothing valid to say."""
    out = []
    for f in (finds if isinstance(finds, list) else [])[:MAX_REPORTS]:
        if not isinstance(f, dict):
            continue
        name, what = plain(f.get("name"), 60), plain_sentence(f.get("what"), 300)
        if name:
            out.append(f"• {name}: {what}" if what else f"• {name}")
    if not out:
        return None
    them = "it" if len(out) == 1 else "them"
    return "\n".join(out) + f"\n\nI'll scan {them} and write the report. About {minutes(profile, len(out))} min."


def run(workdir: str, relay_factory=Relay) -> int:
    work = Path(workdir)
    try:
        job = parse_job((work / "job.json").read_text(encoding="utf-8"))
        finds = json.loads((work / "finds.json").read_text(encoding="utf-8")).get("finds")
        try:
            profile = json.loads((work / "profile.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profile = {}
        text = build(finds, profile if isinstance(profile, dict) else {})
        if text:
            relay_factory(job.relay_url).send(job.chat_id, text)
    except (OSError, ValueError, AttributeError, RelayError) as e:
        print(f"reelay: summary skipped ({type(e).__name__})")  # the run carries on
    except Exception as e:  # noqa: BLE001
        print(f"reelay: summary skipped ({type(e).__name__})")
    return 0
