"""Can this Mac reach Telegram over HTTPS? School and work Wi-Fi often filters it.

A filter that re-signs TLS makes certificate checks fail while other sites still work.
We never turn certificate checks off, and the request carries no token (just the bare host).
The bot itself talks to Telegram through httpx, which trusts the `certifi` bundle, so we use
the same bundle here when it's installed. Even so a pass is not a guarantee: the bot's own
startup message is the source of truth.
"""

import socket
import ssl
import urllib.error
import urllib.request

from .checks import Check

URL = "https://api.telegram.org/"
NEUTRAL_URL = "https://example.com/"  # a boring site, to tell "Telegram is filtered" from "all HTTPS is broken"
NAME = "Can reach Telegram"
FILTERED = ("This network is filtering Telegram (common on school/work Wi-Fi). "
            "Use another network or a phone hotspot.")
INTERFERING = ("Something on this network is interfering with secure connections to Telegram "
               "(common on school/work Wi-Fi). Use another network or a phone hotspot.")
NO_CERTS = ("Your Mac can't verify the security certificate of any website, so this isn't about Telegram. "
            "Check the date and time in System Settings > General > Date & Time. If they are right, "
            "tell whoever gave you Reelay that Python has no trusted certificates.")
TIMEOUT = "Telegram didn't answer in time. Check your internet connection."
UNREACHABLE = "I couldn't reach Telegram. Check your internet connection."
DNS_BLOCKED = "I couldn't find Telegram. Check your internet connection, or this network may be blocking Telegram."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 302 from the host is already proof we can talk to it


def _context() -> ssl.SSLContext | None:
    """Default verification, using certifi like the bot does. Never weakened."""
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _open(url: str, timeout: float):
    handlers = [_NoRedirect]
    context = _context()
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(url, timeout=timeout)


def _is_timeout(e: BaseException) -> bool:
    return isinstance(e, (TimeoutError, socket.timeout)) or isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout))


def _reason(e: BaseException) -> BaseException:
    return getattr(e, "reason", None) if isinstance(e, urllib.error.URLError) else e


def _cert_problem(opener, timeout: float, error: ssl.SSLError) -> Check:
    """Telegram's certificate failed. Is it only Telegram, or every HTTPS site?"""
    if not isinstance(error, ssl.SSLCertVerificationError):
        return Check(NAME, False, "secure connection failed", INTERFERING)
    try:
        opener(NEUTRAL_URL, timeout).close()
    except urllib.error.HTTPError:
        pass  # the neutral site answered: only Telegram is the problem
    except Exception as e:  # noqa: BLE001 - any other failure of the neutral site
        if isinstance(_reason(e), ssl.SSLCertVerificationError):
            return Check(NAME, False, "no website's certificate can be verified", NO_CERTS)
    return Check(NAME, False, "certificate check failed", FILTERED)


def check_telegram(opener=_open, timeout: float = 5) -> Check:
    """One quick HTTPS request. Any HTTP answer (even an error page) means Telegram is reachable.
    Never raises: a flaky network must not crash `reelay doctor` or `reelay setup`."""
    try:
        opener(URL, timeout).close()
        return Check(NAME, True, "yes")
    except urllib.error.HTTPError:
        return Check(NAME, True, "yes")
    except Exception as e:  # noqa: BLE001 - garbage replies raise http.client errors, odd hosts UnicodeError
        reason = _reason(e)
        if isinstance(reason, ssl.SSLError):
            return _cert_problem(opener, timeout, reason)
        if _is_timeout(e):
            return Check(NAME, False, "timed out", TIMEOUT)
        if isinstance(reason, socket.gaierror):
            return Check(NAME, False, "couldn't look up the address", DNS_BLOCKED)
        return Check(NAME, False, "connection failed", UNREACHABLE)
