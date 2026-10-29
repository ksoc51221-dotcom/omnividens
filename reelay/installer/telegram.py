"""One tiny Telegram call: ask "who is this bot?" so we can build the pairing link.

The token goes only to api.telegram.org (that is the whole point of a bot token).
Error messages here never include the request URL, because the URL contains the token.
"""

import json
import re
import urllib.error
import urllib.request

TOKEN_SHAPE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")


class TokenRejected(Exception):
    """Telegram says this token is not valid."""


class NetworkProblem(Exception):
    """Couldn't reach Telegram (offline, blocked, slow)."""


def get_bot_username(token: str, opener=urllib.request.urlopen) -> str:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with opener(url, timeout=10) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise (TokenRejected() if e.code in (401, 404) else NetworkProblem()) from None
    except (OSError, ValueError):
        raise NetworkProblem() from None
    if not isinstance(data, dict) or not data.get("ok"):
        raise TokenRejected()
    username = (data.get("result") or {}).get("username")
    if not isinstance(username, str) or not username:
        raise TokenRejected()
    return username
