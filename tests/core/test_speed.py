"""Speed without losing findings: workers grade concurrently but give the same result;
fast depth prunes only variants that can't add a finding."""

from __future__ import annotations

import pytest

from rlgate.core.task import Patch
from rlgate.ingest import spec
from rlgate.pipeline import audit
from rlgate.settings import CheckSettings, SettingsError, _apply_checks
from rlgate.variants.engine import VariantClass, VariantEngine, VariantResult, pmap
from rlgate.variants.mutate import mutate_variants
from rlgate.variants.revert import Variant

LOOSE = "tests/fixtures/grader_strength/loose_suite"
PYTEST_TASK = "tests/fixtures/forgery/conftest_hijack"


def _key(r):
    return sorted((f.rule_id, f.location or "", f.evidence.detail) for f in r.findings)


@pytest.mark.parametrize("path", [LOOSE, PYTEST_TASK])
def test_workers_change_speed_not_results(path, tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path))
    one = audit(spec.load(path), k=2, include_review=True, keep_going=True, workers=1)
    many = audit(spec.load(path), k=2, include_review=True, keep_going=True, workers=8)
    par = audit(spec.load(path), k=2, include_review=True, workers=8, parallel=True)
    assert _key(one) == _key(many) == _key(par)
    assert [c.probe_id for c in one.checks] == [c.probe_id for c in par.checks]


def test_pmap_keeps_input_order():
    assert pmap(lambda x: x * 2, range(20), 8) == [x * 2 for x in range(20)]


PATCH = Patch(
    "--- a/m.py\n+++ b/m.py\n@@ -1,0 +1,3 @@\n"
    "+if a < b and c < d:\n+    return x + 1 + 2\n+y = 3\n"
    "@@ -10,0 +13,1 @@\n+z = 4 < 5\n"
)


class _Engine(VariantEngine):
    """Revert results are given; grading just records what was asked."""

    def __init__(self, unguarded: set[str], depth: str) -> None:
        self.task = type("T", (), {"gold_patch": PATCH, "instruction": None})()
        self.workers, self.depth, self.pruned = 1, depth, {}
        self.graded: list[str] = []
        self._revert = [VariantResult(Variant("x", "revert", "", o), VariantClass.DISCRIMINATED,
                                      1.0, True) for o in unguarded]
        import threading
        self._lock = threading.Lock()

    def score(self, v):
        self.graded.append(v.origin)
        return VariantResult(v, VariantClass.FAILED_ALL, 0.0, False)


def test_fast_depth_grades_one_mutant_per_operator_per_hunk():
    every = mutate_variants(PATCH)
    e = _Engine(set(), "fast")
    e.run_mutations()
    assert len(e.graded) == len({v.origin for v in every}) < len(every)
    full = _Engine(set(), "full")
    full.run_mutations()
    assert len(full.graded) == len(every)


def test_hunks_proven_untested_are_never_mutated():
    e = _Engine({"m.py#hunk1"}, "full")
    e.run_mutations()
    assert e.graded and not any(o.startswith("m.py#hunk1:") for o in e.graded)
    assert e.pruned["mutation"] > 0


def test_settings_read_workers_and_depth(tmp_path):
    c = CheckSettings()
    _apply_checks(c, {"checks": {"workers": 8, "depth": "full"}}, tmp_path / "rlgate.toml")
    assert (c.workers, c.depth) == (8, "full")
    with pytest.raises(SettingsError):
        _apply_checks(c, {"checks": {"depth": "deep"}}, tmp_path / "rlgate.toml")
