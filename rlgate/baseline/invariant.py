"""Phase B: oracle==1.0 / nop==0.0 at k=5, plus flaky detection. Fail-fast — if the baseline
is not sane, probes that assume a sane baseline are skipped (they'd be noise).

When the grader emits a per-test report, we also consolidate the gold run's per-test map
(test id -> passed) across the k runs and flag any test whose pass/fail flips as flaky. That
map is the search space the variant engine runs its subset against.
"""

from __future__ import annotations

from rlgate.core.context import Baseline
from rlgate.core.task import Task
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.parsers import TestReport, TestStatus, collect


def establish(task: Task, sandbox: Sandbox, k: int = 5) -> Baseline:
    oracle_runs: list[float] = []
    oracle_reports: list[TestReport | None] = []
    for _ in range(k):
        reward, report = _run(task, sandbox, gold=True)
        oracle_runs.append(reward)
        oracle_reports.append(report)
    nop_runs = [_run(task, sandbox, gold=False)[0] for _ in range(k)]

    flaky: set[str] = set()
    if len(set(oracle_runs)) > 1:
        flaky.add("oracle")
    if len(set(nop_runs)) > 1:
        flaky.add("nop")

    per_test, test_flaky = _consolidate(oracle_reports)
    flaky |= test_flaky

    oracle = min(oracle_runs)   # worst-case; a single miss is a flaky/unfair signal
    nop = max(nop_runs)
    return Baseline(
        oracle=oracle, nop=nop, k=k, flaky_checks=frozenset(flaky), per_test=per_test
    )


def _run(task: Task, sandbox: Sandbox, gold: bool) -> tuple[float, TestReport | None]:
    patch = task.gold_patch.text if (gold and task.gold_patch) else None
    with sandbox.overlay(patch) as ovl:
        res = ovl.exec(task.grader.run)
        report = collect(ovl, task.grader.report) if task.grader.report else None
    reward = task.grader.pass_reward if res.exit_code == 0 else 0.0
    return reward, report


def _consolidate(
    reports: list[TestReport | None],
) -> tuple[dict[str, bool], set[str]]:
    """Merge k gold reports into {test id -> passed} + the set of flaky (flipping) tests.

    A test passes iff every run where it appeared recorded PASS (SKIP is ignored). A test that
    is both PASS and FAIL across runs is flaky; it is excluded from the usable per-test map."""
    present = [r for r in reports if r is not None]
    if not present:
        return {}, set()

    seen: dict[str, set[TestStatus]] = {}
    for report in present:
        for name, status in report.status_by_name.items():
            seen.setdefault(name, set()).add(status)

    per_test: dict[str, bool] = {}
    flaky: set[str] = set()
    for name, statuses in seen.items():
        graded = statuses - {TestStatus.SKIP}
        if TestStatus.PASS in graded and TestStatus.FAIL in graded:
            flaky.add(name)
            continue
        per_test[name] = graded == {TestStatus.PASS}
    return per_test, flaky
