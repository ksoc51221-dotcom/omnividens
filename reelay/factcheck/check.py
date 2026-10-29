"""fact_check(): run a backend, validate its answer, never raise."""

import logging
import subprocess

from reelay.core.config import ConfigError, load_config
from reelay.core.types import Profile, Verdict, VideoBundle
from reelay.factcheck.backends import Backend, make_backend
from reelay.factcheck.parse import failed, parse_verdict

log = logging.getLogger(__name__)


def fact_check(bundle: VideoBundle, profile: Profile, backend: Backend | None = None,
               config: dict | None = None) -> Verdict:
    """Watch the video (frames + transcript + caption) and return a validated Verdict."""
    try:
        if backend is None:
            backend = make_backend(config if config is not None else load_config())
        raw = backend.run(bundle, profile)
    except subprocess.TimeoutExpired:
        return failed("it took too long")
    except ConfigError as e:
        return failed(str(e)[:150])
    except FileNotFoundError:
        return failed("the claude program was not found")
    except Exception as e:  # noqa: BLE001 - contract: never raise
        log.warning("fact-check backend failed: %s: %s", type(e).__name__, str(e)[:200])
        return failed(f"{type(e).__name__}")
    return parse_verdict(raw, bundle.duration_s)
