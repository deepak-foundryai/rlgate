"""End to end: mutation survivors are REVIEW, and redundant mutants are pruned.

loose_suite checks g(1) > 2, so removing g's hunk fails (the hunk is guarded) but `x + 3`
survives. weak_suite never checks g at all: removing g's hunk already PROVES it untested
(RG101), so its mutants are pruned instead of reported again."""

from rlgate.core.finding import VerdictTier
from rlgate.ingest import spec
from rlgate.pipeline import audit

LOOSE = "tests/fixtures/grader_strength/loose_suite"
WEAK = "tests/fixtures/grader_strength/weak_suite"


def _muts(path: str):
    result = audit(spec.load(path), k=1, only={"grader_strength.mutation_probe"},
                   include_review=True)
    return [f for f in result.findings if f.probe_id == "grader_strength.mutation_probe"]


def test_mutation_survivors_are_review_on_the_loosely_checked_hunk():
    muts = _muts(LOOSE)
    assert muts, "expected a surviving mutant on the loosely checked g-hunk"
    assert all(f.verdict is VerdictTier.REVIEW for f in muts)
    assert all("hunk1" in f.location for f in muts)
    assert {f.location.rsplit(":", 1)[-1] for f in muts} == {"increment"}


def test_mutants_inside_a_hunk_proven_untested_are_pruned():
    assert _muts(WEAK) == []
