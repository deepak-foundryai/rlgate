"""The one interface every check implements. A probe is a self-contained module in
``rlgate/probes/<family>/`` decorated with ``@register`` — discovery picks it up."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Finding, Severity
from rlgate.core.task import Capability, Task


class ProbeKind(Enum):
    GENERIC = "generic"   # free with the engine (runs on any task with the capabilities)
    PATTERN = "pattern"   # per-grader pattern library (§3.6–§3.8); provable once written


@dataclass(frozen=True)
class ProbeMeta:
    id: str                                    # stable, e.g. "forgery.world_readable_tests"
    family: str
    kind: ProbeKind
    severity: Severity
    needs_live: bool = False                   # requires a live run (e.g. race probes)
    languages: frozenset[str] | None = None    # None = language-agnostic
    requires: frozenset[Capability] = field(default_factory=frozenset)  # task must provide ALL
    doc: str = ""                              # one-line summary → probe-catalog.md


class Probe(ABC):
    meta: ProbeMeta

    def applicable(self, task: Task) -> bool:
        """Gate on CAPABILITIES, never on task.source / benchmark identity."""
        caps_ok = self.meta.requires <= task.capabilities
        lang_ok = self.meta.languages is None or bool(self.meta.languages & task.languages)
        return caps_ok and lang_ok

    @abstractmethod
    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        """Deterministic generation; stable verdicts. Return zero or more findings."""
        raise NotImplementedError
