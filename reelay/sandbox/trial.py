"""Orchestrates one trial: fetch -> scan -> (maybe) run -> measure -> clean up."""

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reelay.core.types import Candidate, TrialReport
from reelay.sandbox import fetch, routing, runner as runner_mod, safety, scan
from reelay.sandbox.runner import RunResult, Runner

log = logging.getLogger(__name__)
LOCAL_HEADS_UP = "🖥️ Testing this on your Mac with a small local model. It can take ~5–10 min."
SCAN_ONLY_FIT = "Scan-only: safety scan done, live trial skipped"
NO_BACKEND_HINT = " — set up Ollama (free) or a Pro token (`claude setup-token`) to try it for real."


def run_trial(
    candidate: Candidate,
    runner: Runner | None = None,
    fetcher=fetch.clone,
    scanner=scan.scan,
    timeout_s: int = 180,
    root: Path | None = None,
    keep: bool = False,
    probe=None,
    launch=None,
    allow_local: bool = True,
    notify: Callable[[str], None] | None = None,
) -> TrialReport:
    """Trial `candidate` and return a report. Never raises for bad candidates.

    A custom `runner` runs once, as given. Without one, the backend is chosen by `routing`
    (`probe` -> which backends work now, `launch` -> run one backend; tests pass fakes).
    `allow_local=False` keeps this trial off the local model (the pipeline allows one per video).
    `notify` gets a one-line heads-up before a slow local run.
    """
    routed = None if runner else Routed(probe or default_probe, launch or runner_mod.run_backend, allow_local, notify)
    # mkdtemp makes a private (0700) folder in the system temp dir, away from ~/.reelay.
    run_dir = Path(tempfile.mkdtemp(prefix="reelay-sandbox-", dir=root))
    repo = run_dir / "repo"
    try:
        return _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo, routed)
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)


def _trial(candidate, runner, fetcher, scanner, timeout_s, run_dir, repo, routed) -> TrialReport:
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

    if routed:
        return _routed_run(routed, repo, run_dir, timeout_s, verdict, note, report, t0)
    try:
        result = runner(repo, run_dir, timeout_s)
    except Exception as e:  # a broken runner must not crash the pipeline
        return report(verdict, True, False, 0, time.monotonic() - t0, "The trial run crashed.", f"{note}\n{e!r}")
    fit = ("Ran in isolation; see logs for what it did." if result.ok
           else "It did not run cleanly in isolation.")
    return report(verdict, True, result.ok, result.tokens, result.seconds, fit, f"{note}\n{result.output}"[:4000])


@dataclass
class Routed:
    probe: Callable[[], dict[str, bool]]
    launch: Callable[..., RunResult]
    allow_local: bool = True
    notify: Callable[[str], None] | None = None


def default_probe() -> dict[str, bool]:
    """Which backends could run right now. Ollama is only asked when it could be used."""
    cfg = runner_mod.trial_backend()
    return {
        "subscription": runner_mod.find_oauth_token() is not None,
        "api": runner_mod.has_api_key(),
        "local": cfg in ("auto", "local") and runner_mod.ollama_status(runner_mod.local_model())[0],
    }


def _secrets() -> list[str]:
    return [k for k in (runner_mod.find_api_key(), runner_mod.find_oauth_token()) if k]


def _attempt(routed: Routed, backend: str, repo, run_dir, timeout_s) -> RunResult:
    """One run on one backend. Claude-backed runs are counted against today's cap first."""
    if backend in routing.CLAUDE_BACKENDS:
        routing.record_claude_trial()
    model = runner_mod.local_model() if backend == "local" else runner_mod.trial_model()
    budget = runner_mod.max_trial_usd() if backend == "api" else None
    if backend == "local":
        timeout_s = max(timeout_s, routing.LOCAL_MIN_TIMEOUT_S)  # small models on a laptop are slow
        if routed.notify:
            try:
                routed.notify(LOCAL_HEADS_UP)
            except Exception:  # noqa: BLE001 - a failed chat message must not stop the trial
                log.exception("heads-up failed")
    try:
        res = routed.launch(backend, repo, run_dir, timeout_s, model, budget)
    except Exception as e:  # a broken backend must not crash the pipeline
        return RunResult(False, 0.0, 0, runner_mod.redact(f"The trial run crashed: {e!r}", _secrets()), backend, model)
    res.backend, res.model = res.backend or backend, res.model or model
    return res


def _routed_run(routed: Routed, repo, run_dir, timeout_s, verdict, note, report, t0) -> TrialReport:
    configured = runner_mod.trial_backend()
    available = routed.probe()
    cap = runner_mod.max_claude_trials_per_day()
    used = routing.claude_used_today()
    blocked = "" if routed.allow_local else "this video already had its one local trial"
    backend, reason = routing.choose_backend(repo, available, used, cap, configured, blocked)
    if backend == "none":
        hint = NO_BACKEND_HINT if not any(available.values()) else "."
        return report(verdict, True, False, 0, 0.0, f"{SCAN_ONLY_FIT} ({reason}){hint}", note)

    sub_dir = run_dir / "attempt1"
    sub_dir.mkdir(mode=0o700)
    result = _attempt(routed, backend, repo, sub_dir, timeout_s)
    tokens, seconds, logs = result.tokens, result.seconds, [result.output]
    escalated = False
    if (not result.ok and backend == "local" and configured == "auto" and routing.classify(repo)[0]):
        claude = next((b for b in routing.CLAUDE_BACKENDS if available.get(b)), None)
        if claude and routing.claude_used_today() < cap:
            retry_dir = run_dir / "attempt2"
            retry_dir.mkdir(mode=0o700)
            first_note = f"local attempt failed ({result.output[:120]!r})"
            result = _attempt(routed, claude, repo, retry_dir, timeout_s)
            tokens, seconds = tokens + result.tokens, seconds + result.seconds
            logs = [first_note, result.output]
            reason, escalated = f"escalated from local, which failed on this light skill; {reason}", True
    head = f"Ran in isolation on {result.backend} ({result.model}) — {reason}"
    if not result.ok:
        head = f"It did not run cleanly in isolation on {result.backend} ({result.model}) — {reason}"
    if result.backend == "api" and result.cost_usd is not None:
        head += f" · ${result.cost_usd:.4f} (cap ${runner_mod.max_trial_usd():.2f})"
    elif result.backend == "api":
        head += f" · cap ${runner_mod.max_trial_usd():.2f}"
    return report(verdict, True, result.ok, tokens, seconds, head + ".", f"{head}\n{note}\n" + "\n".join(logs)[:3500])
