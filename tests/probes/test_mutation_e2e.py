"""End-to-end on the weak-suite fixture. The grader only checks f(1)==2, so mutations of the
g-hunk (increment, stub-return) survive → REVIEW findings on hunk1; f-hunk mutants are killed."""

from rlgate.core.finding import VerdictTier
from rlgate.ingest import spec
from rlgate.pipeline import audit

DESCRIPTOR = "tests/fixtures/grader_strength/weak_suite"


def test_mutation_survivors_are_review_on_the_unchecked_hunk():
    task = spec.load(DESCRIPTOR)
    result = audit(task, k=1, only={"grader_strength.mutation_probe"})

    muts = [f for f in result.findings if f.probe_id == "grader_strength.mutation_probe"]
    assert muts, "expected surviving mutants on the unchecked g-hunk"
    assert all(f.verdict is VerdictTier.REVIEW for f in muts)
    assert all("hunk1" in f.location for f in muts)   # g is hunk1; f (hunk0) is guarded
    assert {f.location.rsplit(":", 1)[-1] for f in muts} <= {"increment", "stub_return"}
