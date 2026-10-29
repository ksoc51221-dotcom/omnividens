"""Write ~/.reelay/config.toml safely: private (0600), atomic, old file kept as a backup.

Only simple values are supported (text, numbers, true/false, lists, small tables), which is
all Reelay's config uses. Secrets are never printed; this module prints nothing at all.
"""

import os
import re
from pathlib import Path

from reelay.core.config import ConfigError, config_path, load_config, reelay_home

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _quote(text: str) -> str:
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _quote(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(i) for i in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(i)}" for k, i in v.items()) + " }"
    raise TypeError(f"unsupported value of type {type(v).__name__}")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _quote(k)


def dumps(config: dict) -> str:
    return "".join(f"{_key(k)} = {_value(v)}\n" for k, v in config.items())


def private_dir(path: Path) -> None:
    """Create the folder and make it owner-only (mkdir's mode is ignored if it already exists)."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path == reelay_home():
        os.chmod(path, 0o700)


def _write_private(path: Path, data: bytes) -> None:
    """Create a fresh owner-only file (never inherits another file's looser mode)."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def save_config(updates: dict, path: Path | None = None) -> Path | None:
    """Merge `updates` into the config file. Returns the backup path if an old file existed."""
    path = path or config_path()
    merged = load_config(path)  # raises ConfigError if the old file is unreadable
    merged.update(updates)
    try:
        text = dumps(merged)
    except TypeError as e:
        raise ConfigError(f"Your existing {path} has a setting I can't safely rewrite ({e}). "
                          "Nothing was changed.") from e
    private_dir(path.parent)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        _write_private(backup, path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    _write_private(tmp, text.encode("utf-8"))
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return backup
