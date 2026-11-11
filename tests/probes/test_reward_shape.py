"""Reward-shape conformance linter (A1–A7) over the declarative scoring schema."""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, ScoringSpec, Task
from rlgate.probes.reward_shape.conformance import RewardShapeConformance
from rlgate.scoring import load_model

FIX = Path("tests/fixtures/reward_shape")


def _task(scoring_path: str | None, schema: str = "declarative") -> Task:
    ss = ScoringSpec(schema=schema, path=scoring_path) if scoring_path else None
    return Task(id="t", source="fixture", repo_dir=FIX,
                grader=GraderSpec(run="grade"), scoring_spec=ss)


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def _codes(findings) -> set[str]:
    return {f.title[1:3] for f in findings}   # "[A4] ..." -> "A4"


def test_all_seven_pathologies_fire():
    findings = RewardShapeConformance().run(_task(str(FIX / "debt.scoring.yaml")), _ctx())
    assert _codes(findings) == {"A1", "A2", "A3", "A4", "A5", "A6", "A7"}
    assert all(f.verdict is VerdictTier.CONFIRMED for f in findings)


def test_clean_spec_is_quiet():
    findings = RewardShapeConformance().run(_task(str(FIX / "clean.scoring.yaml")), _ctx())
    assert findings == []


def test_probe_noop_without_scoring_spec():
    assert RewardShapeConformance().run(_task(None), _ctx()) == []


def test_unknown_schema_yields_no_model():
    task = _task(str(FIX / "debt.scoring.yaml"), schema="nonexistent_schema")
    assert load_model(task) is None
    assert RewardShapeConformance().run(task, _ctx()) == []


def test_capability_gating():
    probe = RewardShapeConformance()
    assert probe.applicable(_task(str(FIX / "debt.scoring.yaml")))   # SCORING_SPEC present
    assert not probe.applicable(_task(None))                          # no scoring spec
