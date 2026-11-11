"""Light config helpers. A dependency-free .env loader so DAYTONA_* keys (and anything else)
are available without exporting them. Never overrides an already-set var.

Looked up in order (first value wins): the shell environment, ``./.env``, the ``.env`` beside
each rlgate.toml in use (nearest first), then the user-level ``~/.config/rlgate/.env``
(``$XDG_CONFIG_HOME/rlgate``, ``$RLGATE_HOME``, or the file ``$RLGATE_DOTENV``) — so
``rlgate`` works from any directory."""

from __future__ import annotations

import os
from pathlib import Path

ORIGINS: dict[str, str] = {}     # key → the .env file it came from (for `rlgate doctor`)


def origin(key: str) -> str | None:
    """Where ``key`` came from: a .env path, "shell", or None if unset."""
    if key in ORIGINS:
        return ORIGINS[key]
    return "shell" if os.environ.get(key) else None


_DIRS: list[Path] = []           # folders of the rlgate.toml files in use


def add_dotenv_dir(d: str | Path) -> None:
    """Also read ``<d>/.env`` (settings.load registers each config file's folder)."""
    p = Path(d).expanduser().resolve()
    if p in _DIRS:
        _DIRS.remove(p)
    _DIRS.insert(0, p)               # later config files are nearer: read them first


def user_dotenv() -> Path:
    if os.environ.get("RLGATE_DOTENV"):
        return Path(os.environ["RLGATE_DOTENV"]).expanduser()
    from rlgate.settings import user_dir
    return user_dir() / ".env"


def load_dotenv(path: str | Path | None = None) -> None:
    if path is None:
        load_dotenv(".env")
        for d in _DIRS:
            load_dotenv(d / ".env")
        load_dotenv(user_dotenv())
        return
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = val
            ORIGINS[key] = str(p)
