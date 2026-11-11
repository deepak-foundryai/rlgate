"""End-to-end: audit the weak-suite fixture. The grader only checks f, so removing the g-hunk
still passes → exactly one unguarded-hunk finding; the f-hunk is caught (good coverage)."""

from rlgate.ingest import spec
from rlgate.pipeline import audit

DESCRIPTOR = "tests/fixtures/grader_strength/weak_suite"


def test_revert_finds_the_unguarded_hunk():
    task = spec.load(DESCRIPTOR)
    result = audit(task, k=1, only={"grader_strength.revert_probe"})

    revert = [f for f in result.findings if f.probe_id == "grader_strength.revert_probe"]
    assert len(revert) == 1, f"expected 1 unguarded hunk, got {[f.title for f in revert]}"
    assert "hunk" in revert[0].location  # the g-hunk


def test_baseline_is_sane_on_fixture():
    task = spec.load(DESCRIPTOR)
    result = audit(task, k=3)
    assert result.baseline_sane, [f.title for f in result.findings]
