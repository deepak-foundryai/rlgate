"""Generate variants, build-gate them, run on the per-test subset, classify, and score.

Classification (ARCHITECTURE / PLAN §3.4):
  - did_not_build : patch didn't apply, or the build/compile failed  → NOT coverage signal
  - failed_all    : every subset test failed (refactor/rename hunk)   → NOT coverage signal
  - discriminated : built and some tests passed, some failed          → the real signal
A *building* revert/stub survivor that still scores pass is a reproduced exploit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rlgate.core.context import Baseline
from rlgate.core.task import Task
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.local import PatchConflict
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


class VariantEngine:
    def __init__(self, task: Task, sandbox: Sandbox, baseline: Baseline) -> None:
        self.task = task
        self.sandbox = sandbox
        self.baseline = baseline

    def run_revert(self) -> list[VariantResult]:
        assert self.task.gold_patch is not None
        results: list[VariantResult] = []
        for v in revert_variants(self.task.gold_patch):
            results.append(self._score(v))
        return results

    def _score(self, v: Variant) -> VariantResult:
        try:
            overlay = self.sandbox.overlay(v.patch_text)
        except PatchConflict:
            return VariantResult(v, VariantClass.DID_NOT_BUILD, 0.0, False)
        with overlay:
            run = overlay.exec(self.task.grader.run)
            if run.exit_code == 127 or "No such file" in run.stderr:
                return VariantResult(v, VariantClass.DID_NOT_BUILD, 0.0, False)
            reward = self._reward_from(run.exit_code)
            # pass despite a missing fix hunk → exploit; full fail → good coverage.
            if reward >= self.task.grader.pass_reward:
                return VariantResult(v, VariantClass.DISCRIMINATED, reward, True)
            if reward <= 0.0:
                return VariantResult(v, VariantClass.FAILED_ALL, reward, False)
            return VariantResult(v, VariantClass.DISCRIMINATED, reward, False)

    def _reward_from(self, exit_code: int) -> float:
        return self.task.grader.pass_reward if exit_code == 0 else 0.0

    @staticmethod
    def discrimination_score(results: list[VariantResult]) -> float:
        """Fraction of *building* variants the grader correctly rejected (higher = stronger)."""
        built = [r for r in results if r.klass is not VariantClass.DID_NOT_BUILD]
        if not built:
            return 1.0
        caught = sum(1 for r in built if not r.is_exploit)
        return caught / len(built)
