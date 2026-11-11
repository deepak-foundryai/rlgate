"""Every check against real tasks from data/ (see tests/cases/build.py for each case).

Quick cases run offline. Full-run cases (the task-works gate) need Docker or Daytona and
real money/time, so they run only with RLGATE_LIVE=1.
"""

from __future__ import annotations

import os

import pytest

from rlgate.core import rules
from tests.cases import build as B

pytestmark = pytest.mark.skipif(not B.available(), reason="data/terminal-bench is not "
                                "present")

QUICK = [c for c in B.ALL if not c.live]
LIVE = [c for c in B.ALL if c.live]


@pytest.fixture(scope="session", autouse=True)
def _built():
    if B.available():
        B.build()


def _found(case: B.Case, *, full: bool = False) -> set[str]:
    from rlgate.ingest import spec
    from rlgate.pipeline import audit

    task = spec.load(case.path)
    # keep_going: a case lists every rule it plants, even past the step a run stops at
    if full:
        result = audit(task, k=3, include_review=True, keep_going=True)
    else:
        result = audit(task, static_only=True, include_review=True, keep_going=True)
    return {f.rule_id for f in result.findings}


@pytest.mark.parametrize("case", QUICK, ids=lambda c: c.name)
def test_quick_case(case: B.Case):
    if not case.source.exists():
        pytest.skip(f"{case.source} is not present")
    got = _found(case)
    assert case.expect <= got, f"{case.name}: expected {sorted(case.expect)}, got {sorted(got)}"
    assert not (case.absent & got), f"{case.name}: must not find {sorted(case.absent & got)}"


@pytest.mark.skipif(os.environ.get("RLGATE_LIVE") != "1",
                    reason="full runs need Docker or Daytona; set RLGATE_LIVE=1")
@pytest.mark.parametrize("case", LIVE, ids=lambda c: c.name)
def test_full_run_case(case: B.Case):
    got = _found(case, full=True)
    assert case.expect <= got, f"{case.name}: expected {sorted(case.expect)}, got {sorted(got)}"


def test_every_rule_has_a_case_or_a_reason():
    covered = set().union(*(c.expect for c in B.ALL))
    missing = set(rules.RULES) - covered - set(B.NOT_COVERED)
    assert not missing, f"rules with no case and no reason in NOT_COVERED: {sorted(missing)}"
    stale = covered & set(B.NOT_COVERED)
    assert not stale, f"covered now, remove from NOT_COVERED: {sorted(stale)}"


def test_planted_cases_change_only_one_thing():
    """A planted case's extra findings (beyond what its untouched source already has) are
    exactly the rules it plants: the change introduces nothing else."""
    from rlgate.ingest import spec
    from rlgate.pipeline import audit

    for c in QUICK:
        if c.plant is None:
            continue
        before = {f.rule_id for f in audit(spec.load(c.source), static_only=True,
                                           include_review=True, keep_going=True).findings}
        added = _found(c) - before
        assert added == set(c.expect) - before, f"{c.name}: also added {sorted(added - c.expect)}"


def test_rlgate_limit_is_a_quick_run_with_a_reason_not_a_finding(capsys, monkeypatch):
    from rlgate.cli import main

    case = B.BY_NAME["limit-sidecar-artifacts"]
    code = main(["check", str(case.path)])
    cap = capsys.readouterr()
    out, err = " ".join(cap.out.split()), " ".join(cap.err.split())
    assert "quick run" in out
    assert "the rest needs a full run (artifact" in out
    assert "a full run isn't possible" in err
    assert "RG605" not in out and code == 0
    assert "a full run builds and grades the task" not in out


def test_shared_runtime_shows_under_step_2_baseline(capsys):
    from rlgate.cli import main

    main(["check", str(B.BY_NAME["rg004-shared-runtime"].path), "--quick", "-v"])
    out = " ".join(capsys.readouterr().out.split())
    assert "Step 2 Baseline ? to review" in out
    assert "Check the grader runs apart from the agent" in out


def test_case_manifest_is_written():
    text = (B.CASES / "CASES.md").read_text()
    for c in B.ALL:
        assert f"| {c.name} |" in text
