"""The baseline stops after two agreeing runs prove a task broken; anything else gets all k."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlgate.baseline import invariant
from rlgate.core.task import GraderSpec, Patch, Task


def _task() -> Task:
    return Task(id="t", source="fixture", repo_dir=Path("."), grader=GraderSpec(run="grade"),
                gold_patch=Patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"))


def _fake(monkeypatch, gold: list[float], nop: list[float]) -> list[bool]:
    calls: list[bool] = []
    seq = {True: iter(gold), False: iter(nop)}

    def run(task, sandbox, gold, fresh=False):
        calls.append(gold)
        return next(seq[gold]), None, 1.0
    monkeypatch.setattr(invariant, "_run", run)
    return calls


def test_a_reference_that_fails_twice_stops_after_three_gradings(monkeypatch):
    calls = _fake(monkeypatch, gold=[0.0] * 5, nop=[0.0] * 5)
    b = invariant.establish(_task(), object(), k=5)          # type: ignore[arg-type]
    assert len(calls) == 3 and b.early and not b.sane and b.k == 2
    [f] = [f for f in invariant.findings(_task(), b) if f.rule_id == "RG001"]
    assert "skipped" in f.evidence.detail


def test_doing_nothing_passing_twice_stops_too(monkeypatch):
    calls = _fake(monkeypatch, gold=[1.0] * 5, nop=[1.0] * 5)
    b = invariant.establish(_task(), object(), k=5)          # type: ignore[arg-type]
    assert len(calls) == 3 and b.early and b.nop == 1.0


def test_a_sane_task_gets_every_run(monkeypatch):
    calls = _fake(monkeypatch, gold=[1.0] * 5, nop=[0.0] * 5)
    b = invariant.establish(_task(), object(), k=5)          # type: ignore[arg-type]
    assert len(calls) == 10 and not b.early and b.sane and b.k == 5


@pytest.mark.parametrize("gold", [[0.0, 1.0, 1.0, 1.0, 1.0], [0.0, 0.5, 0.0, 0.0, 0.0]])
def test_disagreeing_runs_mean_flaky_so_all_k_run(monkeypatch, gold):
    calls = _fake(monkeypatch, gold=gold, nop=[0.0] * 5)
    b = invariant.establish(_task(), object(), k=5)          # type: ignore[arg-type]
    assert len(calls) == 10 and not b.early and "oracle" in b.flaky_checks
