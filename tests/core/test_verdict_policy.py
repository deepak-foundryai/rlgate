"""Verdict policy: the library asserts only what it PROVES. REVIEW findings are suppressed
by default and only appear with include_review / --include-review."""

from __future__ import annotations

from rlgate.core.finding import VerdictTier
from rlgate.ingest import spec
from rlgate.pipeline import audit

WEAK = "tests/fixtures/grader_strength/loose_suite"  # a mutant survives: REVIEW


def test_default_suppresses_review():
    result = audit(spec.load(WEAK), k=1)
    assert all(f.verdict is VerdictTier.CONFIRMED for f in result.findings)
    # the probe still ran — it's recorded as a check, just not flagged
    assert any(c.probe_id == "grader_strength.mutation_probe" for c in result.checks)


def test_include_review_surfaces_review():
    result = audit(spec.load(WEAK), k=1, include_review=True)
    assert any(f.verdict is VerdictTier.REVIEW for f in result.findings)


def test_a_check_that_crashes_costs_that_check_not_the_task(monkeypatch):
    # an rlgate bug in one check (a patch it couldn't parse) failed the whole task and
    # reported it Broken, dropping every other check's result
    from rlgate.pipeline import CheckRun
    from rlgate.probes.grader_strength import mutation_probe

    def boom(self, task, ctx):
        raise ValueError("Target without source: +++ b/x copy.json")
    monkeypatch.setattr(mutation_probe.MutationProbe, "run", boom)
    result = audit(spec.load(WEAK), k=1)
    (c,) = [c for c in result.checks if c.probe_id == "grader_strength.mutation_probe"]
    assert c.status == CheckRun.UNFINISHED and "Target without source" in c.reason
    assert len(result.checks) > 1
