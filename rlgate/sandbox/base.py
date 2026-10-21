"""Execution substrate. Backends (local/docker/daytona) implement this ABC.

The key operations the probes need: build once, snapshot, cheap per-variant overlays, exec,
inspect permissions, and kill stray candidate processes (forgery/race probes)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class Perms:
    mode: int             # e.g. 0o755
    exists: bool = True

    @property
    def world_readable(self) -> bool:
        return self.exists and bool(self.mode & 0o004)


class Overlay(ABC):
    """A cheap, disposable copy of the workspace with a patch applied."""

    root: Path

    @abstractmethod
    def exec(self, cmd: str, timeout: int = 900) -> ExecResult: ...

    @abstractmethod
    def read_perms(self, path: str) -> Perms: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Sandbox(ABC):
    @abstractmethod
    def build(self) -> None:
        """Build the task image / prepare the pristine workspace once."""

    @abstractmethod
    def overlay(self, patch_text: str | None = None) -> Overlay:
        """A fresh overlay on the snapshot, optionally with a patch applied (zero fuzz)."""

    @abstractmethod
    def pkill_user(self, user: str) -> None:
        """Kill detached candidate processes before scoring (race-probe defense)."""
