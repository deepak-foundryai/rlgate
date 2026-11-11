"""Shared, expensive state built once and handed to every probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlgate.review.session import Session
    from rlgate.sandbox.base import Sandbox
    from rlgate.variants.engine import VariantEngine


@dataclass
class Baseline:
    """Phase B result: oracle/nop at k runs, flaky checks, and the per-test→region map."""

    oracle: float                       # gold reward (want 1.0)
    nop: float                          # empty-submission reward (want 0.0)
    k: int
    flaky_checks: frozenset[str] = frozenset()   # checks that disagreed across k runs
    per_test: dict[str, bool] = field(default_factory=dict)  # test id → passed under gold
    ok: bool = False                    # oracle==pass_reward and nop==0.0 and no flaky checks
    pass_reward: float = 1.0            # the grader's full-success reward
    oracle_runs: tuple[float, ...] = ()  # every gold reward (so reports can say "5/5")
    nop_runs: tuple[float, ...] = ()
    # per-test bookkeeping (only when the grader writes a per-test report)
    nop_per_test: dict[str, bool] = field(default_factory=dict)  # test id → passed, no fix
    reports: bool = False               # both sides produced a per-test report
    # timings against the task's own limits
    grade_seconds: float = 0.0          # slowest grading run
    grade_limit: float = 0.0
    solve_seconds: float = 0.0          # slowest reference-solution run (solution scripts)
    solve_limit: float = 0.0
    # the reference's workspace changes, extracted as a patch and graded alone
    extracted: float | None = None      # its reward; None = not applicable / not run
    # False: the task ships no reference solution (RL data often doesn't). Only the
    # do-nothing runs happened; ``oracle`` is a placeholder and the reference checks are off
    reference: bool = True

    @property
    def fail_to_pass(self) -> list[str]:
        return sorted(t for t, ok in self.per_test.items()
                      if ok and not self.nop_per_test.get(t, False))

    @property
    def pass_to_pass(self) -> list[str]:
        return sorted(t for t, ok in self.per_test.items()
                      if ok and self.nop_per_test.get(t) is True)

    @property
    def broken_by_fix(self) -> list[str]:
        """Tests that pass on the unchanged task and fail with the reference fix."""
        return sorted(t for t, ok in self.nop_per_test.items()
                      if ok and self.per_test.get(t) is False)

    @property
    def gold_passes(self) -> int:
        return sum(r >= self.pass_reward - 1e-9 for r in self.oracle_runs)

    @property
    def nop_fails(self) -> int:
        return sum(r == 0.0 for r in self.nop_runs)

    @property
    def sane(self) -> bool:
        return (self.oracle >= self.pass_reward and self.nop == 0.0
                and not self.flaky_checks)


@dataclass
class ProbeContext:
    sandbox: Sandbox
    baseline: Baseline
    variants: VariantEngine
    review: Session | None = None        # --deep-review: the LLM review panel (rlgate.review)
    config: dict = field(default_factory=dict)
