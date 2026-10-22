"""The normalized environment contract. Every loader produces a ``Task``; every probe
consumes one. Probes gate on ``Task.capabilities`` — never on a benchmark name."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path

try:
    from unidiff import PatchSet
except ImportError:  # keep import-time soft so `list-probes` works without extras
    PatchSet = None  # type: ignore


class Capability(Enum):
    """What a task *provides*. Probes require these; this is the only thing they gate on."""

    GOLD_PATCH = "gold_patch"      # a reference fix with addressable hunks
    GIT_REPO = "git_repo"          # workspace is a git checkout
    CONTAINER = "container"        # grader runs in a container we control
    SCORING_SPEC = "scoring_spec"  # a declared reward formula (any schema plugin)
    PER_TEST = "per_test"          # grader emits per-test results (junit/trx/go-json/…)
    INSTRUCTION = "instruction"    # an agent-facing prompt exists
    # extend freely — adding a capability is additive.


@dataclass(frozen=True)
class Patch:
    """A unified diff. Canonicalized + hashed once so hunk decomposition is stable."""

    text: str

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @cached_property
    def patchset(self) -> PatchSet:
        if PatchSet is None:
            raise RuntimeError("unidiff is required to parse patches (pip install rlgate[dev])")
        return PatchSet(self.text)


@dataclass(frozen=True)
class ReportSpec:
    format: str          # "junit" | "trx" | "vitest_json" | "go_json" | "cpp_exit"
    path: str            # where the grader writes it (inside the sandbox)


@dataclass(frozen=True)
class GraderSpec:
    """How to score a submission. Image optional (local run); report optional (per-test)."""

    run: str                        # command that scores the current workspace
    image: str | None = None        # Dockerfile path or image ref; None → local sandbox
    report: ReportSpec | None = None
    pass_reward: float = 1.0        # reward when the run signals full success
    select: str | None = None       # subset-run template with a {tests} slot (cost opt);
                                     # needs PER_TEST. None → always run the full `run`.

    @property
    def capabilities(self) -> set[Capability]:
        caps: set[Capability] = set()
        if self.image:
            caps.add(Capability.CONTAINER)
        if self.report:
            caps.add(Capability.PER_TEST)
        return caps


@dataclass(frozen=True)
class ScoringSpec:
    schema: str          # name of a registered ScoringSchema plugin
    path: str            # path to the scoring file(s) this schema reads


@dataclass(frozen=True)
class Task:
    id: str
    source: str                                 # free-form provenance; NOT a probe switch
    repo_dir: Path                              # the as-shipped workspace
    grader: GraderSpec
    instruction: str | None = None
    gold_patch: Patch | None = None
    scoring_spec: ScoringSpec | None = None
    languages: frozenset[str] = frozenset()
    meta: dict = field(default_factory=dict)    # source-specific; generic probes never read it

    @cached_property
    def capabilities(self) -> frozenset[Capability]:
        caps: set[Capability] = set()
        if self.gold_patch:
            caps.add(Capability.GOLD_PATCH)
        if self.scoring_spec:
            caps.add(Capability.SCORING_SPEC)
        if self.instruction:
            caps.add(Capability.INSTRUCTION)
        if (self.repo_dir / ".git").exists():
            caps.add(Capability.GIT_REPO)
        caps |= self.grader.capabilities
        return frozenset(caps)
