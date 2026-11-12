"""The normalized environment contract. Every loader produces a ``Task``; every probe
consumes one. Probes gate on ``Task.capabilities`` — never on a benchmark name."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlgate.runtime.spec import RuntimeSpec

from unidiff.patch import PatchSet


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
        return PatchSet(self.text)


@dataclass(frozen=True)
class ReportSpec:
    format: str          # "junit" | "trx" | "vitest_json" | "go_json" | "cpp_exit"
    path: str            # where the grader writes it (inside the sandbox)


@dataclass(frozen=True)
class EnvSpec:
    """One container environment: built from a Dockerfile, pulled as an image, or a
    multi-service compose stack whose commands run in ``service``.

    Used when the workspace lives INSIDE an image (``Task.workspace_env``: the agent's view is
    the image's ``workdir``) and when the grader runs in its own environment
    (``GraderSpec.env``: a separate verifier image or stack with the tests baked in)."""

    dockerfile: Path | None = None
    image: str | None = None
    compose: Path | None = None
    service: str = "main"                # compose: the service commands run in
    workdir: str | None = None           # absolute; None → the image's own default
    cpus: float | None = None
    memory_mb: int | None = None
    storage_mb: int | None = None
    build_timeout_s: float = 1800.0
    network: bool = True                 # outbound network while grading

    @property
    def context(self) -> Path | None:
        """The build context directory (what a cache key must cover)."""
        if self.compose is not None:
            return self.compose.parent
        return self.dockerfile.parent if self.dockerfile is not None else None

    @property
    def describe(self) -> str:
        if self.compose is not None:
            return f"compose stack {self.compose} (service {self.service!r})"
        if self.dockerfile is not None:
            return f"image built from {self.dockerfile}"
        return f"image {self.image}"


@dataclass(frozen=True)
class GraderSpec:
    """How to score a submission. Image optional (local run); report optional (per-test)."""

    run: str                        # command that scores the current workspace
    image: str | None = None        # Dockerfile path or image ref; None → local sandbox
    report: ReportSpec | None = None
    pass_reward: float = 1.0        # reward when the run signals full success
    select: str | None = None       # subset-run template with a {tests} slot (cost opt);
                                     # needs PER_TEST. None → always run the full `run`.
    verdict: str = "exit_code"      # how the REAL grader decides pass (core/grade.py):
                                     # "exit_code" | "report" (no failed test, ≥1 passed) |
                                     # "both" (exit 0 AND report clean) |
                                     # "reward_file" (the grader writes its reward to a file)
    reward_files: tuple[str, ...] = ()   # verdict=reward_file: tried in order; .json holds
                                          # {name: number}, anything else one number
    reward_key: str = "reward"      # which .json entry is THE reward (else the only one)
    env: EnvSpec | None = None      # a separate grading environment (tests baked in); None →
                                     # grade where the workspace lives
    artifacts: tuple[str, ...] = ()  # absolute paths carried from the workspace into a
                                     # separate grading env — the only things it ever sees
    inject: tuple[tuple[str, str], ...] = ()   # (host dir, container dir) uploaded just
                                                # before grading: held-out tests
    collect: tuple[tuple[str, int], ...] = ()  # (command, timeout s) run where the agent
                                                # worked, after it finishes and before its
                                                # artifacts are taken (Harbor
                                                # [[verifier.collect]], e.g. writing a patch)
    cwd: str | None = None          # where `run` executes; None → the workspace / workdir
    timeout_s: int = 900
    env_vars: tuple[tuple[str, str], ...] = ()

    @property
    def capabilities(self) -> set[Capability]:
        caps: set[Capability] = set()
        if self.image or self.env:
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
    runtime: RuntimeSpec | None = None          # how to stand up/grade (ingest may declare it;
                                                 # else detect_runtime derives it structurally)
    origin: str | None = None                   # path the task was loaded from (for `rlgate
                                                 # repro` commands); None for bank rows
    workspace_env: EnvSpec | None = None        # the workspace lives in this image at its
                                                 # workdir; ``repo_dir`` is then only the
                                                 # on-disk view (build context) until a
                                                 # sandbox materializes the real one
    oracle: tuple[str, str] | None = None       # (host dir, script): a solution SCRIPT run
                                                 # in the workspace; gold patch derived from it

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
        if self.workspace_env is not None:
            caps.add(Capability.CONTAINER)
        caps |= self.grader.capabilities
        return frozenset(caps)

    @property
    def test_roots(self) -> list[tuple[Path, str]]:
        """(directory, display prefix) pairs holding graded tests: the workspace, each
        held-out test dir the grader uploads at grading time, and the build context of a
        verifier image the tests are baked into (Harbor's ``tests/`` either way), so
        file-only checks see the tests a full run would grade."""
        roots = [(self.repo_dir, "")]
        dirs = [Path(host) for host, _ in self.grader.inject]
        env = self.grader.env       # a verifier image built from the tests (baked in)
        if env is not None and env.context is not None and (
                self.workspace_env is None or env.context != self.workspace_env.context):
            dirs.append(env.context)
        seen = {self.repo_dir.resolve()}
        for p in dirs:
            if p.is_dir() and p.resolve() not in seen:
                seen.add(p.resolve())
                roots.append((p, p.name + "/"))
        return roots

    @property
    def staged(self) -> bool:
        """The workspace must be pulled out of an image and/or graded in a separate
        environment — run by ``sandbox.staged``, not the copy-a-directory backends."""
        return self.workspace_env is not None or self.grader.env is not None
