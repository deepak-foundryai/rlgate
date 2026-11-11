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

    def before_grade(self) -> None:
        """Called by ``run_grader`` right before the grader runs — where held-out material
        (tests) arrives, AFTER anything the candidate did. Default: nothing to stage."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Sandbox(ABC):
    @abstractmethod
    def build(self) -> None:
        """Build the task image / prepare the pristine workspace once."""

    @abstractmethod
    def overlay(self, patch_text: str | None = None, *, isolated: bool = False) -> Overlay:
        """A fresh overlay on the snapshot, optionally with a patch applied (zero fuzz).

        The workspace is always fresh. ``isolated=True`` additionally guarantees nothing
        outside the workspace (site-packages, /tmp, running processes) is shared with any
        other overlay — required by payloads that write outside it. Backends that reuse one
        machine across overlays (Daytona) honour it with a dedicated machine."""

    @abstractmethod
    def pkill_user(self, user: str) -> None:
        """Kill detached candidate processes before scoring (race-probe defense)."""

    def agent_env(self) -> Overlay:
        """A fresh copy of what the agent sees at the start of a run, to inspect (container
        backends: the workspace inside the task image). Not for grading."""
        return self.overlay(None)

    def close(self) -> None:
        """Release everything this sandbox holds (remote machines, temp dirs). Idempotent."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
