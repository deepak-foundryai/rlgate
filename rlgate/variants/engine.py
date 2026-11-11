"""Generate variants, build-gate them, run on the per-test subset, classify, and score.

Classification (ARCHITECTURE / PLAN §3.4):
  - did_not_build : patch didn't apply, or the build/compile failed  → NOT coverage signal
  - failed_all    : every subset test failed (refactor/rename hunk)   → NOT coverage signal
  - discriminated : built and some tests passed, some failed          → the real signal
A *building* revert/stub survivor that still scores pass is a reproduced exploit.

Reward resolution, in order of sharpness:
  1. per-test report present → a variant passes iff no gold-passing test regressed, and we can
     tell FAILED_ALL (every gold-passing test broke) from DISCRIMINATED (only some did);
  2. no report → the shared grade (core/grade.py: exit code / report per grader.verdict).

Subset execution: when the grader declares a ``select`` template and the baseline gives a
per-test map, each variant runs only the gold-passing tests (``select.format(tests=...)``)
instead of the whole suite — the cost optimization, with the exploit signal preserved.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from rlgate.core.context import Baseline
from rlgate.core.grade import GradeResult, run_grader
from rlgate.core.task import Task
from rlgate.sandbox._patch import PatchConflict
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.parsers import TestStatus
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
    """Scores variants of the gold fix. Two things keep it fast without losing a finding:

    - ``workers``: independent variants are graded concurrently (each in its own overlay);
      results come back in input order, so output stays deterministic;
    - pruning (``depth="fast"``, the default): a hunk whose removal already passes is PROVEN
      unguarded, so mutating or stubbing inside it adds nothing; and per hunk only the first
      mutant of each operator is graded (``depth="full"`` grades every site).
    """

    def __init__(self, task: Task, sandbox: Sandbox, baseline: Baseline, *,
                 workers: int = 1, depth: str = "fast") -> None:
        self.task = task
        self.sandbox = sandbox
        self.baseline = baseline
        self.workers = max(1, workers)
        self.depth = depth
        self._lock = threading.Lock()
        self._revert: list[VariantResult] | None = None
        self.pruned: dict[str, int] = {}      # probe → variants skipped as redundant

    def run_revert(self) -> list[VariantResult]:
        """Every hunk removed in turn. Computed once and shared (mutation and stub pruning
        read it), whichever probe asks first."""
        assert self.task.gold_patch is not None
        with self._lock:
            if self._revert is None:
                self._revert = self.run(revert_variants(self.task.gold_patch))
            return self._revert

    def unguarded(self) -> set[str]:
        """Origins (``path#hunkN``) of hunks whose removal still scores pass."""
        return {r.variant.origin for r in self.run_revert()
                if r.is_exploit and r.klass is VariantClass.DISCRIMINATED}

    def unguarded_lines(self) -> dict[str, set[int]]:
        """path → the post-fix line numbers covered by unguarded hunks."""
        assert self.task.gold_patch is not None
        bad = self.unguarded()
        out: dict[str, set[int]] = {}
        for pf in self.task.gold_patch.patchset:
            for i, h in enumerate(pf):
                if f"{pf.path}#hunk{i}" in bad:
                    out.setdefault(pf.path, set()).update(
                        range(h.target_start, h.target_start + max(h.target_length, 1)))
        return out

    def run_mutations(self) -> list[VariantResult]:
        """Single-token / single-line mutants of the gold fix (relational, logical, boolean,
        off-by-one, negated or dropped branch, stub-return). Survivors are REVIEW-tier — see
        ``variants/mutate.py``. Pruned as the class docstring says."""
        assert self.task.gold_patch is not None
        variants = mutate_variants(self.task.gold_patch)
        total = len(variants)
        bad = self.unguarded()
        variants = [v for v in variants if v.origin.rsplit(":", 1)[0] not in bad]
        if self.depth != "full":
            seen: set[str] = set()
            kept = []
            for v in variants:               # origin = path#hunkN:operator
                if v.origin not in seen:
                    seen.add(v.origin)
                    kept.append(v)
            variants = kept
        self.pruned["mutation"] = total - len(variants)
        return self.run(variants)

    def run_equivalence(self) -> list[VariantResult]:
        """Free-variation transforms of the gold fix (incidental message strings). The grade
        MUST stay pass; a built variant that drops below pass_reward is a proven over-fit."""
        assert self.task.gold_patch is not None
        variants = equivalence_variants(self.task.gold_patch, self.task.instruction)
        return self.run(variants)

    def run(self, variants: list[Variant]) -> list[VariantResult]:
        """Score variants, ``workers`` at a time; results in input order."""
        return pmap(self.score, variants, self.workers)

    def score(self, v: Variant) -> VariantResult:
        return self._score(v)

    # -- internals -------------------------------------------------------------------

    @property
    def _gold_passing(self) -> list[str]:
        return sorted(t for t, passed in self.baseline.per_test.items() if passed)

    def grader_cmd(self) -> str:
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
            g = run_grader(overlay, self.task, self.grader_cmd())
        if g.did_not_run:
            return VariantResult(v, VariantClass.DID_NOT_BUILD, 0.0, False)
        return self._classify(v, g)

    def _classify(self, v: Variant, g: GradeResult) -> VariantResult:
        pass_reward = self.task.grader.pass_reward
        # a grader that writes its own reward decides pass itself (weighted checks, gates);
        # its per-test report is then only for fail-to-pass / pass-to-pass bookkeeping
        if g.report is not None and self.baseline.per_test and \
                self.task.grader.verdict != "reward_file":
            status = g.report.status_by_name
            graded = [t for t in self._gold_passing if t in status]
            broke = [t for t in graded if status[t] is TestStatus.FAIL]
            if not graded:
                # subset produced no overlap (e.g. collection collapsed) → trust the grade
                return self._from_grade(v, g)
            if not broke:
                # every gold-passing test still passes despite a wrong variant → exploit
                return VariantResult(v, VariantClass.DISCRIMINATED, pass_reward, True, [])
            if len(broke) == len(graded):
                return VariantResult(v, VariantClass.FAILED_ALL, 0.0, False, broke)
            return VariantResult(v, VariantClass.DISCRIMINATED, 0.0, False, broke)

        return self._from_grade(v, g)

    def _from_grade(self, v: Variant, g: GradeResult) -> VariantResult:
        if g.passed:
            return VariantResult(v, VariantClass.DISCRIMINATED, g.reward, True)
        return VariantResult(v, VariantClass.FAILED_ALL, g.reward, False)

    @staticmethod
    def discrimination_score(results: list[VariantResult]) -> float:
        """Fraction of *building* variants the grader correctly rejected (higher = stronger)."""
        built = [r for r in results if r.klass is not VariantClass.DID_NOT_BUILD]
        if not built:
            return 1.0
        caught = sum(1 for r in built if not r.is_exploit)
        return caught / len(built)


def pmap[A, B](fn: Callable[[A], B], items: Iterable[A], workers: int) -> list[B]:
    """``[fn(x) for x in items]``, up to ``workers`` at a time, results in input order.
    The first exception is raised once the others finish (no half-cancelled overlays)."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))
