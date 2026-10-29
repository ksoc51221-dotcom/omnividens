"""Which folders may NOT go on the allow-list. `allowed_projects` is what lets a Telegram
message drive Claude Code, so secrets folders, system folders and anything wide are refused."""

from pathlib import Path

from reelay.core.config import reelay_home

# Inside the user's Home folder: keys, cloud logins, app settings, Reelay's own token file.
SECRET_DIRS = [".ssh", ".aws", ".gnupg", ".config", ".reelay", "Library"]
# Outside Home: system areas (and /Users, which holds everybody's Home).
SYSTEM_ROOTS = ["/Users", "/etc", "/System", "/Library", "/private", "/usr", "/bin", "/sbin", "/var"]


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def forbidden_reason(path: Path, home: Path, reelay_dir: Path | None = None) -> str:
    """Why `path` must not be allowed, or "" if it is fine. `path` need not exist."""
    path, home = path.resolve(), home.resolve()
    if path == Path("/").resolve() or path == home or path in home.parents:
        return "That is too wide. Choose a folder inside it, like ~/Projects/my-app."
    secret_dirs = [home / d for d in SECRET_DIRS] + [(reelay_dir or reelay_home()).resolve()]
    if any(_inside(path, d) for d in secret_dirs):
        return "That folder holds passwords, keys or app settings. Choose a normal project folder."
    if not _inside(path, home) and any(_inside(path, Path(r).resolve()) for r in SYSTEM_ROOTS):
        return "That is a system folder. Choose a normal project folder in your Home folder."
    return ""
