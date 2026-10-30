"""Turn a network failure into one plain sentence that names the host.

Never suggests turning certificate checks off or getting around a filter.
"""

import re
import socket
import ssl
from urllib.parse import urlparse

_NET_NAMES = {"ConnectError", "ConnectTimeout", "ReadTimeout", "TransportError", "NetworkError",
              "ConnectionError", "URLError", "MaxRetryError", "NewConnectionError"}
_HOST_RE = re.compile(r"host='([^']+)'|host=([\w.-]+)|https?://([\w.-]+)")


def _chain(error: BaseException):
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def _is_cert(error: BaseException) -> bool:
    return isinstance(error, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error)


def _is_net(error: BaseException) -> bool:
    return (isinstance(error, (socket.gaierror, ssl.SSLError, ConnectionError, TimeoutError))
            or any(c.__name__ in _NET_NAMES for c in type(error).__mro__))


def is_network_error(error: BaseException) -> bool:
    return any(_is_net(e) for e in _chain(error))


def host_of(error: BaseException, fallback: str = "") -> str:
    """Best guess at the host that failed: from the error if it says, else the link given."""
    for e in _chain(error):
        named = getattr(e, "host", None)  # an error type that carries its host
        if isinstance(named, str) and named:
            return named
        said = re.search(r"reach (\S+) from", getattr(e, "user_message", ""))  # T1's NetworkError wording
        if said and said.group(1) != "that":
            return said.group(1)
        url = getattr(getattr(e, "request", None), "url", None) or getattr(e, "url", None)
        host = getattr(url, "host", None) or (urlparse(url).hostname if isinstance(url, str) else None)
        if host:
            return host
        match = _HOST_RE.search(str(e))
        if match:
            return next(g for g in match.groups() if g)
    return fallback or "the website"


def explain(error: BaseException, fallback_host: str = "") -> str:
    """One friendly sentence. Never includes the raw error text (it can hold URLs with secrets)."""
    host = host_of(error, fallback_host)
    if any(_is_cert(e) for e in _chain(error)):
        return (f"I couldn't reach {host} securely: its certificate wasn't accepted, which often "
                "happens on school or work networks that filter traffic. Try another network, "
                "like a phone hotspot.")
    return f"I couldn't reach {host}: check your internet connection. This network may also block that site."
