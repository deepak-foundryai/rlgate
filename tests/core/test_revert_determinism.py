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


def test_build_output_and_inert_hunks_are_not_parts_of_the_fix(tmp_path):
    # Terminal-Bench references are scripts: the patch rlgate derives from running them
    # picked up .lake/ build files, Coq .aux/.glob and blank-line hunks, and "removing" one
    # of those was reported as "a part of the fix is not tested" (23 false positives)
    (tmp_path / "m.py").write_text(
        'def f(x):\n    """Old doc."""\n    return x + 1\n\n\ndef g():\n    return 2\n')
    patch = (
        "--- a/m.py\n+++ b/m.py\n"
        "@@ -1,3 +1,3 @@\n def f(x):\n-    \"\"\"Old doc.\"\"\"\n+    \"\"\"New doc.\"\"\"\n"
        "     return x + 1\n"
        "@@ -6,2 +6,2 @@\n def g():\n-    return 2\n+    return 3\n"
        "--- a/c.c\n+++ b/c.c\n@@ -1,2 +1,4 @@\n int a;\n+// a comment\n+\n int b;\n"
        "--- a/h.c\n+++ b/h.c\n@@ -1,1 +1,2 @@\n int a;\n+#include <x.h>\n"
        "--- a/.lake/build/ir/Basic.c\n+++ b/.lake/build/ir/Basic.c\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/.Main.aux\n+++ b/.Main.aux\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/.eslintrc.cjs\n+++ b/.eslintrc.cjs\n@@ -1 +1 @@\n-x\n+y\n")
    (tmp_path / "c.c").write_text("int a;\nint b;\n")
    origins = [v.origin for v in revert_variants(Patch(patch), tmp_path)]
    assert origins == ["m.py#hunk1", "h.c#hunk0"]      # the real change; #include is code


def test_inert_hunks_are_judged_by_code_not_by_how_a_line_starts():
    """Review of 0.1.7: a C continuation line `* factor` and `/* x */ return y;` were taken
    for comments, so a real fix hunk got no variant; CMakeLists.txt and a Go `doc/`
    package were taken for docs."""
    from rlgate.core.task import Patch
    from rlgate.variants.revert import generated, revert_variants

    def hunks(path: str, old: str, new: str, ctx: str = "int f() {\n") -> list:
        text = (f"--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@\n " + ctx
                + "".join(f"-{x}\n" for x in old.splitlines())
                + "".join(f"+{x}\n" for x in new.splitlines()))
        other = "--- a/z.c\n+++ b/z.c\n@@ -1 +1 @@\n-int z = 1;\n+int z = 2;\n"
        return [v for v in revert_variants(Patch(text + other)) if not v.origin.startswith("z.c")]
    assert hunks("m.c", "    * factor;", "    * (factor + 1);")           # code
    assert hunks("m.c", "    /* x */ return y;", "    /* x */ return z;")  # code
    assert not hunks("m.c", "    // old note", "    // new note")           # comment
    assert not hunks("m.c", " * old words", " * new words */", ctx="/**\n")   # in a block
    assert hunks("m.py", 'x = """', 'y = """', ctx="def f():\n")             # code
    assert not hunks("run.sh", "# old", "# new", ctx="set -e\n")
    assert hunks("run.sh", "#!/bin/sh", "#!/bin/bash", ctx="\n")             # a shebang
    assert not generated("CMakeLists.txt") and not generated("src/go/doc/comment.go")
    assert not generated("lib/x.d") and generated("README.md")
