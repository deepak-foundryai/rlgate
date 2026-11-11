"""The planted-defect bank: it builds, every task loads and its reference passes, and the
score counts hits and false alarms."""

from __future__ import annotations

import json

from rlgate import planted
from rlgate.cli.engine import load_target
from rlgate.ingest import spec
from rlgate.pipeline import audit


def test_the_bank_builds_and_loads(tmp_path):
    made = planted.build(tmp_path)
    labels = json.loads((tmp_path / "labels.json").read_text())
    assert set(labels) == {c.name for c in planted.CASES} == {d.name for d in made}
    _, tasks = load_target(str(tmp_path))
    assert len(tasks) == len(planted.CASES)


def test_reference_passes_and_doing_nothing_fails(tmp_path):
    planted.build(tmp_path)
    for name in ("clean", "weak-one-input", "hidden-message"):
        r = audit(spec.load(str(tmp_path / name)), k=1, only={"baseline.invariant"})
        assert r.baseline is not None and r.baseline.oracle == 1.0 and r.baseline.nop == 0.0


def test_score_counts_hits_and_false_alarms():
    label = {"expect": ["RG110", "RG109"], "forbid": ["RG704"]}
    assert planted.score_one("x", label, {"RG109", "RG008"}).hit is True
    s = planted.score_one("x", label, {"RG704"})
    assert s.hit is False and s.false_alarms == ["RG704"]
    assert planted.score_one("c", {"expect": [], "forbid": []}, set()).hit is None
    sm = planted.summary([planted.score_one("x", label, {"RG109"}), s,
                          planted.Score("y", None, [], [], skipped="not checked yet")])
    assert sm == {"cases": 3, "ran": 2, "recall": 0.5, "false_alarm_cases": 1}


def test_a_task_without_a_reference_still_runs_every_step(tmp_path):
    """RL data often ships no reference fix: the do-nothing runs and every check that
    doesn't need a reference still run (leaks, forgery, the hack agent); the reference
    checks show as not applicable, and nothing claims the reference failed."""
    import shutil

    from rlgate.pipeline import CheckRun
    src = "tests/fixtures/agentic/shop"
    d = tmp_path / "shop"
    shutil.copytree(src, d)
    y = (d / "task.rlgate.yaml").read_text().replace("gold_patch: ./fix.patch\n", "")
    (d / "task.rlgate.yaml").write_text(y)
    r = audit(spec.load(str(d)), k=2, keep_going=True)
    assert r.baseline is not None and not r.baseline.reference
    assert len(r.baseline.nop_runs) == 2 and r.baseline.oracle_runs == ()
    assert r.stopped_at is None and len(r.steps) == 6
    rules_found = {f.rule_id for f in r.findings}
    assert not rules_found & {"RG001", "RG006", "RG007", "RG008"}
    assert "RG201" in rules_found                  # forgery still checked, with do-nothing
    revert = next(c for c in r.checks if c.probe_id == "grader_strength.revert_probe")
    assert revert.status == CheckRun.NA


def test_keep_going_passes_isolation_failures_but_not_a_broken_baseline(monkeypatch):
    from types import SimpleNamespace as NS

    from rlgate import pipeline
    from rlgate.core import questions as Q

    monkeypatch.setattr(Q, "kind_of", lambda f: Q.PROVEN)
    assert not pipeline.foundation_failed([NS(rule_id="RG010"), NS(rule_id="RG004")])
    assert pipeline.foundation_failed([NS(rule_id="RG010"), NS(rule_id="RG001")])
