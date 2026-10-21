"""Phase B: oracle==1.0 / nop==0.0 at k=5, plus flaky detection. Fail-fast — if the baseline
is not sane, probes that assume a sane baseline are skipped (they'd be noise)."""

from __future__ import annotations

from rlgate.core.context import Baseline
from rlgate.core.task import Task
from rlgate.sandbox.base import Sandbox


def establish(task: Task, sandbox: Sandbox, k: int = 5) -> Baseline:
    oracle_runs = [_run(task, sandbox, gold=True) for _ in range(k)]
    nop_runs = [_run(task, sandbox, gold=False) for _ in range(k)]

    flaky = set()
    if len(set(oracle_runs)) > 1:
        flaky.add("oracle")
    if len(set(nop_runs)) > 1:
        flaky.add("nop")

    oracle = min(oracle_runs)   # worst-case; a single miss is a flaky/unfair signal
    nop = max(nop_runs)
    return Baseline(oracle=oracle, nop=nop, k=k, flaky_checks=frozenset(flaky))


def _run(task: Task, sandbox: Sandbox, gold: bool) -> float:
    patch = task.gold_patch.text if (gold and task.gold_patch) else None
    with sandbox.overlay(patch) as ovl:
        res = ovl.exec(task.grader.run)
    return task.grader.pass_reward if res.exit_code == 0 else 0.0
