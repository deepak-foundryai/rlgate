"""Bundled bank-mapping presets (data, not code). Each ``<name>.bank.yaml`` is a declarative
mapping for a known benchmark layout; a new/unknown layout is handled by a user-supplied
mapping file passed to the bank loader — no code change."""

from __future__ import annotations

from pathlib import Path

from rlgate.ingest.mapping import BankMapping

_DIR = Path(__file__).parent


def preset_names() -> list[str]:
    return sorted(p.name[: -len(".bank.yaml")] for p in _DIR.glob("*.bank.yaml"))


def load_preset(name: str) -> BankMapping:
    path = _DIR / f"{name}.bank.yaml"
    if not path.is_file():
        raise ValueError(f"unknown preset {name!r}; known: {preset_names()}")
    return BankMapping.from_file(path)
