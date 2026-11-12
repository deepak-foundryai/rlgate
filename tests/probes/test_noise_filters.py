"""Regressions from the 0.1.6 benchmark review: checks that fired on code no grading reads,
on words in docstrings, and on files the image build wrote."""

from __future__ import annotations

import os
from pathlib import Path

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.task import GraderSpec, Patch, Task
from rlgate.probes.forgery.unrestored_test_infra import UnrestoredTestInfra
from rlgate.probes.leakage.mtime_clustering import MtimeClustering
from rlgate.probes.test_quality._graded import graded_tests, runs_pytest
from rlgate.variants._code import mask as _mask
from rlgate.variants.mutate import mutate_variants

GOLD = Patch("--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,2 @@\n-x = 0\n+x = 1\n+y = 2\n")


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def _task(repo: Path, run: str = "pytest -q", inject: tuple = (), **kw) -> Task:
    return Task(id="t", source="fixture", repo_dir=repo, gold_patch=GOLD,
                grader=GraderSpec(run=run, inject=inject, **kw))


def test_graded_tests_are_the_test_patch_lines_when_there_is_one(tmp_path):
    repo, swe = tmp_path / "repo", tmp_path / "swe"
    repo.mkdir(), swe.mkdir()
    (repo / "test_unrelated.py").write_text("m.assert_called_once()\n")
    (swe / "eval.sh").write_text(
        "git apply - <<'EOF_1'\ndiff --git a/t/test_a.py b/t/test_a.py\n--- a/t/test_a.py\n"
        "+++ b/t/test_a.py\n@@ -1 +1,2 @@\n x = 1\n+assert f() == 'new'\nEOF_1\npytest t\n")
    got = dict(graded_tests(_task(repo, inject=((str(swe), "/rlgate-swe"),))))
    assert got == {"t/test_a.py": "assert f() == 'new'"}


def test_graded_tests_skip_the_workspace_when_tests_are_held_out(tmp_path):
    repo, held = tmp_path / "repo", tmp_path / "tests"
    repo.mkdir(), held.mkdir()
    (repo / "test_repo_own.py").write_text("x\n")
    (held / "test_graded.py").write_text("y\n")
    assert [r for r, _ in graded_tests(_task(repo, inject=((str(held), "/tests"),)))] == [
        "tests/test_graded.py"]
    assert [r for r, _ in graded_tests(_task(repo))] == ["test_repo_own.py"]


def test_unrestored_infra_follows_the_runner_and_the_collected_paths(tmp_path):
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    for f in ("pkg/tests/conftest.py", "other/conftest.py", "conftest.py"):
        (tmp_path / f).write_text("")
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py3\n")         # no [pytest]
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n")
    task = _task(tmp_path, run="pytest pkg", fail_to_pass=("pkg/tests/test_x.py::test_a",))
    got = {f.location for f in UnrestoredTestInfra().run(task, _ctx())}
    assert got == {"conftest.py", "pkg/tests/conftest.py", "setup.cfg"}
    # django's runner, sympy's bin/test: pytest never loads these
    assert UnrestoredTestInfra().run(_task(tmp_path, run="./tests/runtests.py"), _ctx()) == []
    assert runs_pytest(_task(tmp_path, run="bash run.sh")) is None     # unknown: still checked


def test_late_files_ignore_what_the_image_build_wrote(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("")
    for f in ("pkg.egg-info/PKG-INFO", "build/lib/x.py", "pkg.egg-info/SOURCES.txt"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    for p in tmp_path.rglob("*"):
        if p.is_file():
            late = "egg-info" in str(p) or "build" in str(p)
            os.utime(p, (2_000_000_000 if late else 1_000_000_000,) * 2)
    assert MtimeClustering().run(_task(tmp_path), _ctx()) == []
    (tmp_path / "answer.txt").write_text("")
    os.utime(tmp_path / "answer.txt", (2_000_000_000,) * 2)
    (f,) = MtimeClustering().run(_task(tmp_path), _ctx())
    assert f.location == "answer.txt"


def test_mutants_change_code_not_words_flags_or_git_metadata():
    gold = Patch(
        "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,6 @@\n def f(a, b):\n"
        '+    """True when a or b is 2."""\n'
        "+    return a or b  # or 3\n"
        "+@dataclass(frozen=True)\n"
        "+class C: pass\n"
        " x = 1\n"
        "--- a/.git/logs/HEAD\n+++ b/.git/logs/HEAD\n@@ -0,0 +1 @@\n+0 1 a <a> 1 +0000 or\n")
    base = gold.text.splitlines()
    got = {next(ln for ln, b in zip(v.patch_text.splitlines(), base, strict=True) if ln != b)
           for v in mutate_variants(gold)}
    assert got == {"+    return a and b  # or 3", "+    return 0"}


def test_masking_keeps_char_literals_apart_from_rust_lifetimes():
    code, _ = _mask("fn f<'a>(x: &'a str) -> bool { x == 'c' }", ".rs", None)
    assert "x ==     }" in code and "&'a str" in code


def test_a_diff_u_timestamp_is_not_part_of_the_file_name():
    """Regression (FoundryAI run): RG103 reported `models.py\t2026-08-30 01:19:07#hunk0`, so
    the Python-only filters never saw a .py file and the unguarded-hunk pruning never matched."""
    gold = Patch("--- a/m.py\t2026-08-30 01:19:07\n+++ b/m.py\t2026-08-30 01:19:07\n"
                 "@@ -1 +1,2 @@\n x = 1\n+y = 2  # or 3\n")
    (v, w) = mutate_variants(gold)
    assert v.origin == "m.py#hunk0:increment" and w.origin == "m.py#hunk0:decrement"


def test_unrestored_infra_is_quiet_when_the_grader_deletes_it(tmp_path):
    """Regression (FoundryAI run): the grader's test.sh deletes every conftest.py from the
    copy it grades, and removes the visible tests; RG211 still flagged them."""
    repo, held = tmp_path / "repo", tmp_path / "tests"
    (repo / "backend" / "tests_visible").mkdir(parents=True)
    (repo / "backend" / "tests_visible" / "conftest.py").write_text("")
    (repo / "setup.cfg").write_text("[tool:pytest]\n")
    held.mkdir()
    (held / "test.sh").write_text(
        'rm -rf "$DEPLOY_DIR/backend/tests_visible"\n'
        'find "$DEPLOY_DIR" \\( -name sitecustomize.py -o -name conftest.py \\\n'
        "    -o -name 'pytest.ini' \\) -delete\npython -m pytest backend\n")
    task = _task(repo, run="bash /tests/test.sh", inject=((str(held), "/tests"),))
    assert {f.location for f in UnrestoredTestInfra().run(task, _ctx())} == {"setup.cfg"}


def test_golden_test_fixtures_are_not_answer_files(tmp_path):
    """Regression (MiMo run): a Go repo's testdata/*.golden — its own tests' expected
    output, committed in the repo — gave 25 "file named like an answer" findings."""
    from rlgate.probes.leakage.cache_scan import CacheScan
    (tmp_path / "pkg" / "testdata").mkdir(parents=True)
    (tmp_path / "pkg" / "testdata" / "TestList.golden").write_text("x\n")
    assert CacheScan().run(_task(tmp_path), _ctx()) == []
    (tmp_path / "answer_key.txt").write_text("42\n")
    (f,) = CacheScan().run(_task(tmp_path), _ctx())
    assert f.location == "answer_key.txt"


def test_grader_removal_is_read_from_the_command_not_the_whole_line(tmp_path):
    """Review of the RG211 fix: any token on a line with `rm` counted as removed (`rm -rf
    /tmp/b && pytest tests/` excused tests/), and a removed name excused it everywhere."""
    from rlgate.probes.test_quality._graded import grader_removes
    held = tmp_path / "t"
    held.mkdir()

    def removal(script: str):
        (held / "test.sh").write_text(script)
        return grader_removes(_task(tmp_path, run="bash /t/test.sh",
                                    inject=((str(held), "/t"),)))
    r = removal("rm -rf /tmp/build && cd /testbed && pytest tests/\n")
    assert not r.covers("tests/conftest.py")
    r = removal("rm -f /app/tests_visible/conftest.py\n")
    assert r.covers("tests_visible/conftest.py") and not r.covers("conftest.py")
    r = removal('find "$DEPLOY_DIR/backend" \\( -name conftest.py -o -name x \\) -delete\n')
    assert r.covers("backend/a/conftest.py") and not r.covers("cli/conftest.py")
    r = removal('git checkout "$BASE" -- tests/conftest.py\nfor p in $L; do rm -rf -- "$p"; done\n')
    assert r.covers("tests/conftest.py") and not r.covers("other/conftest.py")


def test_conftests_and_ini_pytest_really_loads(tmp_path):
    """Review of the RG211 fix: `pytest tests/` loads sibling conftests even when the
    graded ids sit under tests/unit, and `tests/pytest.ini` is the ini for `pytest tests/`."""
    for f in ("tests/unit/conftest.py", "tests/integration/conftest.py", "tests/pytest.ini",
              "setup.cfg", "other/conftest.py"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("[tool:pytest]\n" if f == "setup.cfg" else "")
    task = _task(tmp_path, run="pytest tests/", fail_to_pass=("tests/unit/test_a.py::t",))
    got = {f.location for f in UnrestoredTestInfra().run(task, _ctx())}
    assert got == {"tests/unit/conftest.py", "tests/integration/conftest.py", "tests/pytest.ini"}
    # Harbor: pytest runs the held-out /tests only, so nothing in the repo is loaded
    harbor = _task(tmp_path, run="pytest /tests/test_outputs.py")
    assert UnrestoredTestInfra().run(harbor, _ctx()) == []
    # a grader that doesn't say what it runs: every conftest stays, and the root's ini
    got = {f.location for f in UnrestoredTestInfra().run(_task(tmp_path, run="make check"),
                                                         _ctx())}
    assert got == {"tests/unit/conftest.py", "tests/integration/conftest.py",
                   "other/conftest.py", "setup.cfg"}
