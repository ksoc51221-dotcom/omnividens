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


class RelayError(Exception):
    pass


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
            raise RelayError(f"relay answered {e.code} for {method} {path.split('/')[1:3]}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RelayError(f"relay unreachable ({type(e).__name__})") from None

    def send(self, chat_id: int, text: str, adopt_line: str | None = None, report: bool = False) -> None:
        """`report=True` counts the message against the relay's daily report budget."""
        body = {"chat_id": chat_id, "text": text, "adopt_line": adopt_line}
        if report:
            body["report"] = True
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
