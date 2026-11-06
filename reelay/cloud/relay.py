"""Tiny client for the relay's routine API. urllib only.

The cloud proxy adds `Authorization: Bearer RELAY_SECRET` for the relay host, so we send no
auth header ourselves. Only if the env var RELAY_SECRET is set (local testing) do we add it.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

MAX_FILE_BYTES = 20 * 1024 * 1024
TIMEOUT_S = 60
MAX_BUNDLE_BYTES = 2 * 1024 * 1024


class RelayError(Exception):
    status: int | None = None  # HTTP status when the relay answered with one


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # never follow: a redirect could leak the bearer header
        return None


def check_relay_url(url) -> str:
    p = urlparse(url) if isinstance(url, str) else None
    if p is None or p.scheme != "https" or not p.hostname or p.username or p.password:
        raise RelayError("relay_url must be a plain https URL")
    return url.rstrip("/")


class Relay:
    def __init__(self, relay_url: str):
        self.base = check_relay_url(relay_url)
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, method: str, path: str, body: dict | None = None):
        headers = {"user-agent": "reelay-cloud"}
        secret = os.environ.get("RELAY_SECRET", "").strip()
        if secret:
            headers["authorization"] = f"Bearer {secret}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            return self._opener.open(req, timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            err = RelayError(f"relay answered {e.code} for {method} {path.split('/')[1:3]}")
            err.status = e.code
            raise err from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RelayError(f"relay unreachable ({type(e).__name__})") from None

    def send(self, chat_id: int, text: str, adopt_line: str | None = None, report: bool = False,
             adopt_note: str | None = None) -> None:
        """`report=True` counts the message against the relay's daily report budget."""
        body = {"chat_id": chat_id, "text": text, "adopt_line": adopt_line}
        if report:
            body["report"] = True
        if adopt_note and adopt_line:
            body["adopt_note"] = adopt_note
        with self._request("POST", "/api/send", body):
            pass

    def get_file(self, file_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with self._request("GET", "/api/file/" + quote(file_id, safe="")) as resp, open(dest, "wb") as out:
            while chunk := resp.read(1 << 16):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise RelayError("file is over 20 MB")
                out.write(chunk)
        return dest

    def get_profile(self) -> dict:
        try:
            with self._request("GET", "/api/profile") as resp:
                data = json.loads(resp.read(70_000))
        except (RelayError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def put_profile(self, profile: dict) -> None:
        with self._request("PUT", "/api/profile", profile):
            pass

    def defer(self, job: dict) -> dict:
        """Ask the relay to queue a blocked link job for the owner's Mac. The relay tells the user."""
        with self._request("POST", "/api/defer", {"job": job}) as resp:
            data = json.loads(resp.read(10_000))
        return data if isinstance(data, dict) else {}

    def get_bundle(self, bundle_id: str) -> dict:
        """The Mac's upload: {meta, frames:[{time_s, jpeg_b64}]}. Untrusted until prepare validates it."""
        with self._request("GET", "/api/bundle/" + quote(bundle_id, safe="")) as resp:
            raw = resp.read(MAX_BUNDLE_BYTES + 1)
        try:
            data = json.loads(raw) if len(raw) <= MAX_BUNDLE_BYTES else None
        except ValueError:
            data = None
        if data is None:
            raise RelayError("the Mac's upload was unreadable")
        return data if isinstance(data, dict) else {}
