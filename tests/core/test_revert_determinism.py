"""The determinism guarantee (ARCHITECTURE §2a): same gold patch → identical variant set and
IDs, every run."""

from pathlib import Path

from rlgate.core.task import Patch
from rlgate.variants.revert import revert_variants

FIX = Path("tests/fixtures/grader_strength/weak_suite/fix.patch").read_text()


def test_one_variant_per_hunk():
    variants = revert_variants(Patch(FIX))
    assert len(variants) == 2  # the fixture has exactly two hunks


def test_generation_is_deterministic():
    a = revert_variants(Patch(FIX))
    b = revert_variants(Patch(FIX))
    assert [v.id for v in a] == [v.id for v in b]
    assert [v.patch_text for v in a] == [v.patch_text for v in b]


def test_ids_are_content_addressed():
    ids = {v.id for v in revert_variants(Patch(FIX))}
    assert len(ids) == 2  # distinct hunks → distinct ids


def test_each_variant_drops_exactly_one_hunk():
    for v in revert_variants(Patch(FIX)):
        hunk_headers = sum(1 for ln in v.patch_text.splitlines() if ln.startswith("@@"))
        assert hunk_headers == 1  # 2 hunks minus 1 == 1
