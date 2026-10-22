"""Generate variants, build-gate them, run on the per-test subset, classify, and score.

Classification (ARCHITECTURE / PLAN §3.4):
  - did_not_build : patch didn't apply, or the build/compile failed  → NOT coverage signal
  - failed_all    : every subset test failed (refactor/rename hunk)   → NOT coverage signal
  - discriminated : built and some tests passed, some failed          → the real signal
A *building* revert/stub survivor that still scores pass is a reproduced exploit.

Reward resolution, in order of sharpness:
  1. per-test report present → a variant passes iff no gold-passing test regressed, and we can
     tell FAILED_ALL (every gold-passing test broke) from DISCRIMINATED (only some did);
  2. no report → the grader's exit code (0 → pass_reward).

Subset execution: when the grader declares a ``select`` template and the baseline gives a
per-test map, each variant runs only the gold-passing tests (``select.format(tests=...)``)
instead of the whole suite — the cost optimization, with the exploit signal preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rlgate.core.context import Baseline
from rlgate.core.task import Task
from rlgate.sandbox.base import ExecResult, Overlay, Sandbox
from rlgate.sandbox.local import PatchConflict
from rlgate.sandbox.parsers import TestStatus, collect
from rlgate.variants.equivalence import equivalence_variants
from rlgate.variants.mutate import mutate_variants
from rlgate.variants.revert import Variant, revert_variants


class VariantClass(Enum):
    DID_NOT_BUILD = "did_not_build"
    FAILED_ALL = "failed_all"
    DISCRIMINATED = "discriminated"


@dataclass
class VariantResult:
    variant: Variant
    klass: VariantClass
    reward: float                 # grader reward on this variant (only meaningful if built)
    is_exploit: bool              # built + scored pass despite being wrong-by-construction
    broke: list[str] = field(default_factory=list)  # gold-passing tests this variant regressed


class VariantEngine:
    def __init__(self, task: Task, sandbox: Sandbox, baseline: Baseline) -> None:
        self.task = task
        self.sandbox = sandbox
        self.baseline = baseline

    def run_revert(self) -> list[VariantResult]:
        assert self.task.gold_patch is not None
        return [self._score(v) for v in revert_variants(self.task.gold_patch)]

    def run_mutations(self) -> list[VariantResult]:
        """Single-token / single-line mutants of the gold fix (relational, logical, boolean,
        increment, stub-return). Survivors are REVIEW-tier — see ``variants/mutate.py``."""
        assert self.task.gold_patch is not None
        return [self._score(v) for v in mutate_variants(self.task.gold_patch)]

    def run_equivalence(self) -> list[VariantResult]:
        """Free-variation transforms of the gold fix (incidental message strings). The grade
        MUST stay pass; a built variant that drops below pass_reward is a proven over-fit."""
        assert self.task.gold_patch is not None
        variants = equivalence_variants(self.task.gold_patch, self.task.instruction)
        return [self._score(v) for v in variants]

    # -- internals -------------------------------------------------------------------

    @property
    def _gold_passing(self) -> list[str]:
        return sorted(t for t, passed in self.baseline.per_test.items() if passed)

    def _grader_cmd(self) -> str:
        """Subset command when we can (declared template + a gold-passing set); else full."""
        subset = self._gold_passing
        if self.task.grader.select and subset:
            return self.task.grader.select.format(tests=" ".join(subset))
        return self.task.grader.run

    def _score(self, v: Variant) -> VariantResult:
        try:
            overlay = self.sandbox.overlay(v.patch_text)
        except PatchConflict:
            return VariantResult(v, VariantClass.DID_NOT_BUILD, 0.0, False)
        with overlay:
            run = overlay.exec(self._grader_cmd())
            if run.exit_code == 127 or "No such file" in run.stderr:
                return VariantResult(v, VariantClass.DID_NOT_BUILD, 0.0, False)
            return self._classify(v, overlay, run)

    def _classify(self, v: Variant, overlay: Overlay, run: ExecResult) -> VariantResult:
        report = collect(overlay, self.task.grader.report) if self.task.grader.report else None
        pass_reward = self.task.grader.pass_reward

        if report is not None and self.baseline.per_test:
            status = report.status_by_name
            graded = [t for t in self._gold_passing if t in status]
            broke = [t for t in graded if status[t] is TestStatus.FAIL]
            if not graded:
                # subset produced no overlap (e.g. collection collapsed) → trust exit code
                return self._from_exit(v, run)
            if not broke:
                # every gold-passing test still passes despite a reverted hunk → exploit
                return VariantResult(v, VariantClass.DISCRIMINATED, pass_reward, True, [])
            if len(broke) == len(graded):
                return VariantResult(v, VariantClass.FAILED_ALL, 0.0, False, broke)
            return VariantResult(v, VariantClass.DISCRIMINATED, 0.0, False, broke)

        return self._from_exit(v, run)

    def _from_exit(self, v: Variant, run: ExecResult) -> VariantResult:
        pass_reward = self.task.grader.pass_reward
        reward = pass_reward if run.exit_code == 0 else 0.0
        if reward >= pass_reward:
            return VariantResult(v, VariantClass.DISCRIMINATED, reward, True)
        return VariantResult(v, VariantClass.FAILED_ALL, reward, False)

    @staticmethod
    def discrimination_score(results: list[VariantResult]) -> float:
        """Fraction of *building* variants the grader correctly rejected (higher = stronger)."""
        built = [r for r in results if r.klass is not VariantClass.DID_NOT_BUILD]
        if not built:
            return 1.0
        caught = sum(1 for r in built if not r.is_exploit)
        return caught / len(built)
