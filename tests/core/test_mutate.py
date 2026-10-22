"""Mutation generation is a pure, deterministic function of the patch bytes."""

from __future__ import annotations

from rlgate.core.task import Patch
from rlgate.variants.mutate import mutate_variants

PATCH = Patch(
    "--- a/m.py\n"
    "+++ b/m.py\n"
    "@@ -1,0 +1,2 @@\n"
    "+if a < b and flag == True:\n"
    "+    return x + 1\n"
)

SHIFT = Patch(  # the << digraph must not be read as a relational operator
    "--- a/s.py\n"
    "+++ b/s.py\n"
    "@@ -1,0 +1,1 @@\n"
    "+y = x << 1\n"
)


def test_covers_each_operator_family():
    ops = {v.operator for v in mutate_variants(PATCH)}
    assert ops == {"relational", "logical", "boolean", "increment", "stub_return"}


def test_variants_are_valid_diffs_and_change_the_line():
    for v in mutate_variants(PATCH):
        assert v.patch_text.startswith("--- a/m.py")
        assert v.patch_text != PATCH.text          # something actually mutated
        assert v.patch_text.count("\n") == PATCH.text.count("\n")  # line count preserved


def test_deterministic_ids():
    a = [(v.id, v.operator, v.origin) for v in mutate_variants(PATCH)]
    b = [(v.id, v.operator, v.origin) for v in mutate_variants(PATCH)]
    assert a == b
    assert len({v.id for v in mutate_variants(PATCH)}) == len(a)  # ids unique


def test_digraph_guard_skips_shift():
    ops = [v.operator for v in mutate_variants(SHIFT)]
    assert "relational" not in ops      # the << is not mutated
    assert ops == ["increment"]          # only the integer literal is
