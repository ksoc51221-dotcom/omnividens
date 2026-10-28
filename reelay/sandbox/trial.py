"""Orchestrates one trial: fetch -> scan -> (maybe) run -> measure -> clean up."""

import shutil
import tempfile
import time
from pathlib import Path

from reelay.core.types import Candidate, TrialReport
from reelay.sandbox import fetch, safety, scan
from reelay.sandbox.runner import Runner, claude_runner, has_api_key
SCAN_ONLY_FIT = "Scan-only: safety scan done, live trial skipped — add an API key to try it for real."


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
    can_run = has_api_key() if runner is claude_runner else True
    # mkdtemp makes a private (0700) folder in the system temp dir, away from ~/.reelay.
    run_dir = Path(tempfile.mkdtemp(prefix="reelay-sandbox-", dir=root))
    repo = run_dir / "repo"
    try:
        return _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo, can_run)
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)


def _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo, can_run) -> TrialReport:
    def report(verdict, installed, ran, tokens, seconds, fit, logs) -> TrialReport:
        return TrialReport(candidate, installed, ran, verdict, tokens, seconds, fit, logs)

    t0 = time.monotonic()
    try:
        fetcher(candidate.url, repo)
    except fetch.FetchError as e:
        return report("review", False, False, 0, 0.0, "Could not fetch it, so nothing was tried.", str(e))

    problem = safety.find_problem(repo)
    if problem:
        return report("high", True, False, 0, 0.0,
                      "Refused to run it: " + problem + ".", "safety check: " + problem)

    verdict, note = scanner(repo)
    if verdict == "high":
        return report("high", True, False, 0, 0.0,
                      "Static scan found high-risk patterns, so it was NOT run. Skip unless you audit it.", note)

    if not can_run:
        return report(verdict, True, False, 0, 0.0, SCAN_ONLY_FIT, note)

    try:
        result = runner(repo, run_dir, timeout_s)
    except Exception as e:  # a broken runner must not crash the pipeline
        return report(verdict, True, False, 0, time.monotonic() - t0, "The trial run crashed.", f"{note}\n{e!r}")
    fit = ("Ran in isolation; see logs for what it did." if result.ok
           else "It did not run cleanly in isolation.")
    return report(verdict, True, result.ok, result.tokens, result.seconds, fit, f"{note}\n{result.output}"[:4000])
