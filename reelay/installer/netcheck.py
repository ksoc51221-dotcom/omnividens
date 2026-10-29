"""Can this Mac reach Telegram over HTTPS? School and work Wi-Fi often filters it.

A filter that re-signs TLS makes certificate checks fail while other sites still work.
We never turn certificate checks off, and the request carries no token (just the bare host).
"""

import socket
import ssl
import urllib.error
import urllib.request

from .checks import Check

URL = "https://api.telegram.org/"
NAME = "Can reach Telegram"
FILTERED = ("This network is filtering Telegram (common on school/work Wi-Fi). "
            "Use another network or a phone hotspot.")
TIMEOUT = "Telegram didn't answer in time. Check your internet connection."
UNREACHABLE = "I couldn't reach Telegram. Check your internet connection."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 302 from the host is already proof we can talk to it


def _open(url: str, timeout: float):
    return urllib.request.build_opener(_NoRedirect).open(url, timeout=timeout)


def _is_timeout(e: BaseException) -> bool:
    return isinstance(e, (TimeoutError, socket.timeout)) or isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout))


def check_telegram(opener=_open, timeout: float = 5) -> Check:
    """One quick HTTPS request. Any HTTP answer (even an error page) means Telegram is reachable."""
    try:
        opener(URL, timeout).close()
        return Check(NAME, True, "yes")
    except urllib.error.HTTPError:
        return Check(NAME, True, "yes")
    except ssl.SSLError:
        problem, fix = "certificate check failed", FILTERED
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            problem, fix = "certificate check failed", FILTERED
        elif _is_timeout(e):
            problem, fix = "timed out", TIMEOUT
        else:
            problem, fix = "connection failed", UNREACHABLE
    except OSError as e:
        problem, fix = ("timed out", TIMEOUT) if _is_timeout(e) else ("connection failed", UNREACHABLE)
    return Check(NAME, False, problem, fix)
