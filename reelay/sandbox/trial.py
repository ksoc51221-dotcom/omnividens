"""Orchestrates one trial: fetch -> scan -> (maybe) run -> measure -> clean up."""

import shutil
import time
import uuid
from pathlib import Path

from reelay.core.types import Candidate, TrialReport
from reelay.sandbox import fetch, scan
from reelay.sandbox.runner import Runner, claude_runner

DEFAULT_ROOT = Path("~/.reelay/sandbox_runs")


def run_trial(
    candidate: Candidate,
    runner: Runner | None = None,
    fetcher=fetch.clone,
    scanner=scan.scan,
    timeout_s: int = 180,
    root: Path | None = None,
    keep: bool = False,
) -> TrialReport:
    """Trial `candidate` and return a report. Never raises for bad candidates."""
    runner = runner or claude_runner
    run_dir = (root or DEFAULT_ROOT).expanduser() / uuid.uuid4().hex[:12]
    repo = run_dir / "repo"
    run_dir.mkdir(parents=True, mode=0o700)
    try:
        return _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo)
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)


def _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo) -> TrialReport:
    def report(verdict, installed, ran, tokens, seconds, fit, logs) -> TrialReport:
        return TrialReport(candidate, installed, ran, verdict, tokens, seconds, fit, logs)

    t0 = time.monotonic()
    try:
        fetcher(candidate.url, repo)
    except fetch.FetchError as e:
        return report("review", False, False, 0, 0.0, "Could not fetch it, so nothing was tried.", str(e))

    verdict, note = scanner(repo)
    if verdict == "high":
        return report("high", True, False, 0, 0.0,
                      "Static scan found high-risk patterns, so it was NOT run. Skip unless you audit it.", note)

    try:
        result = runner(repo, run_dir, timeout_s)
    except Exception as e:  # a broken runner must not crash the pipeline
        return report(verdict, True, False, 0, time.monotonic() - t0, "The trial run crashed.", f"{note}\n{e!r}")
    fit = ("Ran in isolation; see logs for what it did." if result.ok
           else "It did not run cleanly in isolation.")
    return report(verdict, True, result.ok, result.tokens, result.seconds, fit, f"{note}\n{result.output}"[:4000])
