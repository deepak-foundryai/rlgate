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


# -- 0.1.8 benchmark review --------------------------------------------------------------


def test_a_patch_in_the_eval_script_is_not_read_as_commands(tmp_path):
    """Regression (SWE-V pytest-6202): `+ import pytest` inside the eval script's heredoc
    patch read as a pytest run with no paths, so every doc/example conftest was flagged."""
    from rlgate.probes.test_quality._graded import collected_paths
    repo, swe = tmp_path / "repo", tmp_path / "swe"
    (repo / "doc" / "en" / "example").mkdir(parents=True)
    (repo / "testing").mkdir()
    (repo / "doc" / "en" / "example" / "conftest.py").write_text("")
    (repo / "testing" / "conftest.py").write_text("")
    swe.mkdir()
    (swe / "eval.sh").write_text(
        "git apply -v - <<'EOF_114329324912'\ndiff --git a/testing/test_c.py "
        "b/testing/test_c.py\n--- a/testing/test_c.py\n+++ b/testing/test_c.py\n"
        "@@ -1 +1,2 @@\n x = 1\n+import pytest\nEOF_114329324912\n"
        "pytest -rA testing/test_collection.py\n")
    task = _task(repo, run="bash /rlgate-swe/eval.sh", inject=((str(swe), "/rlgate-swe"),))
    assert collected_paths(task) == {"testing/test_c.py", "testing/test_collection.py"}
    assert {f.location for f in UnrestoredTestInfra().run(task, _ctx())} == {
        "testing/conftest.py"}


def test_files_the_held_out_test_patch_touches_count_as_restored(tmp_path):
    """Regression (DeepSWE httpx): grader.py resets and re-applies tests/test.patch, which
    rewrites tests/conftest.py; RG211 still called that conftest unrestored."""
    repo, held = tmp_path / "repo", tmp_path / "tests"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "conftest.py").write_text("")
    held.mkdir()
    (held / "test.patch").write_text(
        "diff --git a/tests/conftest.py b/tests/conftest.py\n--- a/tests/conftest.py\n"
        "+++ b/tests/conftest.py\n@@ -0,0 +1 @@\n+import pytest\n")
    (held / "test.sh").write_text("python3 /tests/grader.py prepare\npytest tests\n")
    task = _task(repo, run="bash /tests/test.sh", inject=((str(held), "/tests"),))
    assert UnrestoredTestInfra().run(task, _ctx()) == []


def test_mutants_skip_lean_comments_and_markup_prose():
    """Regression (TB4 Lean, DeepSWE aiomonitor): `-- Axiom 1: ... and ... = true` and
    `<p>Capture and compare ...</p>` gave increment/logical/boolean mutants."""
    gold = Patch(
        "--- a/G.lean\n+++ b/G.lean\n@@ -1 +1,4 @@\n x\n"
        "+-- Axiom 1: B(a, b, c) and E = true\n"
        "+/- docs: 2 or 3\n+  still a comment and 1 -/\n"
        "+theorem t : 1 + 1 = 2 := by rfl\n"
        "--- a/t.html\n+++ b/t.html\n@@ -1 +1,3 @@\n x\n"
        "+<p class=\"mt-2\">Capture and compare snapshots.</p>\n"
        "+{% if n > 1 and ok %}x{% endif %}\n")
    origins = {v.origin.split("#", 1)[0] + ":" + v.patch_text.splitlines()[
        next(i for i, (a, b) in enumerate(zip(v.patch_text.splitlines(),
                                                gold.text.splitlines(), strict=True)) if a != b)]
        for v in mutate_variants(gold)}
    assert not any("Axiom" in o or "docs" in o or "comment" in o or "Capture" in o
                   for o in origins)
    assert any(o.startswith("G.lean:+theorem") for o in origins)
    assert any(o.startswith("t.html:+{% if") for o in origins)


def test_late_files_ignore_cython_output_and_version_files(tmp_path):
    """Regression (SWE-V sklearn): the build's Cython .c files were "late" files."""
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("")
    for f in ("pkg/_iso.pyx", "pkg/_iso.c", "pkg/_version.py", "pkg/_t.pyx.tp", "pkg/_t.pyx"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    for p in tmp_path.rglob("*"):
        if p.is_file():
            late = p.name in ("_iso.c", "_version.py", "_t.pyx")
            os.utime(p, (2_000_000_000 if late else 1_000_000_000,) * 2)
    assert MtimeClustering().run(_task(tmp_path), _ctx()) == []


def test_format_templates_and_instruction_formats_are_not_pinned_wording(tmp_path):
    """Regression (TB4 simplex): `' + {term}'`, `'x{index + 1}'` and a format the
    instruction spells out were "exact-string pinning"."""
    from rlgate.probes.test_quality.brittle_assertions import BrittleAssertions
    repo, held = tmp_path / "repo", tmp_path / "tests"
    repo.mkdir(), held.mkdir()
    lits = ["' + {term}'", "'x{index + 1}'", "', encoding='", "'{n} rows were loaded'",
            "'task could not be found'"]
    (held / "test_out.py").write_text("\n".join(f"assert s == {x}" for x in lits) + "\n")
    gold = Patch("--- a/m.py\n+++ b/m.py\n@@ -1 +1,2 @@\n x\n+L = ["
                 + ", ".join(lits) + "]\n")
    task = Task(id="t", source="fixture", repo_dir=repo, gold_patch=gold,
                instruction="Print `3 rows were loaded` when done.",
                grader=GraderSpec(run="pytest /tests", inject=((str(held), "/tests"),)))
    got = [f.evidence.detail for f in BrittleAssertions().run(task, _ctx())]
    assert len(got) == 1 and "task could not be found" in got[0]


def test_run_outputs_of_the_solution_script_are_not_the_fix(tmp_path):
    """Regression (TB4 batched-eval-parity): `--out /app/results.json` was a hunk of the
    fix ("removing it still passes")."""
    from rlgate.variants._outputs import solution_outputs
    sol, held = tmp_path / "solution", tmp_path / "tests"
    sol.mkdir(), held.mkdir()
    (held / "test_x.py").write_text("open('/app/answer.txt')\n")
    (sol / "solve.sh").write_text(
        "cat > /app/fix.py <<'EOF'\nprint(1) > open('x')\nEOF\n"
        "python run.py --out /app/results.json\npython gen.py > /app/answer.txt\n"
        "node make.js > /app/mem.js\necho 'const m = require(\"./mem.js\")' > /app/vm.js\n"
        "echo hi > /app/note.txt\nsed -i 's/a->b/c/' src/io.cpp\n")
    task = Task(id="t", source="fixture", repo_dir=tmp_path, gold_patch=GOLD,
                instruction="", oracle=(str(sol), "solve.sh"),
                grader=GraderSpec(run="pytest /tests", inject=((str(held), "/tests"),)))
    assert solution_outputs(task) == {"results.json"}


def test_a_new_rlgate_version_reruns_every_check_step(tmp_path, monkeypatch):
    """0.1.8 runs reused whole steps from 0.1.6, so fixed checks still reported old noise."""
    from rlgate.core import store
    task = _task(tmp_path)
    old = store.step_fingerprints(task)
    monkeypatch.setattr(store, "__version__", "9.9.9")
    new = store.step_fingerprints(task)
    assert old[1] == new[1] and all(old[n] != new[n] for n in range(2, 7))


def test_the_process_search_cannot_find_itself():
    """Regression (0.1.8 on Daytona): `pgrep -f <tag>` matched its own `timeout … sh -c
    'pgrep -f <tag>'` wrappers on Linux, so every staged task got RG010."""
    import re
    import subprocess
    from contextlib import contextmanager

    from rlgate.probes.hygiene.carryover import Carryover

    class Ovl:
        def exec(self, cmd, timeout=60):
            r = subprocess.run(["sh", "-c", cmd], check=False, capture_output=True, text=True)
            return type("R", (), {"exit_code": r.returncode})()

    class Box:
        @contextmanager
        def agent_overlay(self, plant):
            yield Ovl()
    ctx = ProbeContext(sandbox=Box(), baseline=Baseline(1.0, 0.0, 1), variants=None)
    assert Carryover().run(_task(Path("/tmp")), ctx) == []
    # the pattern still matches the planted command line
    assert re.search("[r]lgate-carry-abc", "sh -c sleep 100000; : rlgate-carry-abc")


def test_overridden_apis_and_prose_names_are_not_gold_internals(tmp_path):
    """Regression (DeepSWE langchain): the new wrapper overrides Runnable.abatch and
    get_graph (already in the code base), and the instruction says "batch-as-completed"."""
    from rlgate.probes.test_quality.symbol_crossref import SymbolCrossref
    repo, held = tmp_path / "repo", tmp_path / "tests"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "base.py").write_text("class Runnable:\n    def abatch(self): ...\n"
                                           "    def get_graph(self): ...\n")
    held.mkdir()
    (held / "test.patch").write_text(
        "diff --git a/tests/test_c.py b/tests/test_c.py\n--- /dev/null\n+++ b/tests/test_c.py\n"
        "@@ -0,0 +1,3 @@\n+r.abatch(); r.get_graph(); r.batch_as_completed()\n"
        "+helper_secret_mix(r)\n+assert backend.call_count == 1\n")
    gold = Patch("--- a/core/c.py\n+++ b/core/c.py\n@@ -0,0 +1,5 @@\n+class Coal(Runnable):\n"
                 "+    def abatch(self): ...\n+    def get_graph(self): ...\n"
                 "+    def batch_as_completed(self): ...\n+def helper_secret_mix(r): ...\n")
    task = Task(id="t", source="fixture", repo_dir=repo, gold_patch=gold,
                instruction="Coalesce invoke, batch and batch-as-completed calls.",
                grader=GraderSpec(run="bash /tests/test.sh", inject=((str(held), "/tests"),)))
    got = {f.title.split("'")[1] for f in SymbolCrossref().run(task, _ctx())}
    assert got == {"helper_secret_mix"}


def test_a_test_fake_counting_its_own_calls_is_not_a_call_shape(tmp_path):
    """Regression (DeepSWE langchain): a coalescing test checks its fake backend ran once
    (`self.call_count += 1` ... `assert fake.call_count == 1`): that is the behaviour."""
    from rlgate.probes.test_quality.brittle_assertions import BrittleAssertions
    held = tmp_path / "tests"
    held.mkdir()
    (held / "test_c.py").write_text("class Fake:\n    def __call__(self):\n"
                                    "        self.call_count += 1\n"
                                    "def test_x():\n    assert fake.call_count == 1\n")
    task = _task(tmp_path, run="pytest /tests", inject=((str(held), "/tests"),))
    assert BrittleAssertions().run(task, _ctx()) == []
    (held / "test_c.py").write_text("def test_x():\n    m.assert_called_once()\n")
    assert len(BrittleAssertions().run(task, _ctx())) == 1


def test_languages_come_from_the_workspace_pulled_out_of_the_image(tmp_path):
    """Regression (DeepSWE): the build context is a Dockerfile that clones the repo, so the
    task had no language and the Python-only cheating checks (RG201, RG205) never ran,
    though the published audit proved a conftest hook gets full reward."""
    from rlgate.pipeline import prepare
    ctx_dir, ws = tmp_path / "environment", tmp_path / "ws"
    ctx_dir.mkdir(), ws.mkdir()
    (ctx_dir / "Dockerfile").write_text("FROM python:3.12\nRUN git clone x .\n")
    for f in ("a.py", "b.py", "README.md"):
        (ws / f).write_text("")
    task = Task(id="t", source="harbor", repo_dir=ctx_dir, grader=GraderSpec(run="x"))
    box = type("Box", (), {"workspace_dir": ws})()
    got = prepare(task, box)
    assert got.repo_dir == ws and "python" in got.languages


def test_the_runner_script_in_a_test_patch_says_what_pytest_collects(tmp_path):
    """Regression (DeepSWE): the pytest command lives in a test.sh the held-out test.patch
    adds; `require_cmd pytest` in the grader was read as a pytest run with no paths, and
    `-o filterwarnings='ignore::X'` as a test id."""
    from rlgate.probes.test_quality._graded import collected_paths
    repo, held = tmp_path / "repo", tmp_path / "tests"
    (repo / "tests").mkdir(parents=True)
    held.mkdir()
    (held / "test.sh").write_text("require_cmd pytest\nbash /app/test.sh base\n")
    (held / "test.patch").write_text(
        "diff --git a/test.sh b/test.sh\n--- /dev/null\n+++ b/test.sh\n@@ -0,0 +1,2 @@\n"
        "+python -m pytest -o filterwarnings='ignore::Warning' tests/ --ignore=tests/t_x.py\n"
        "+python -m pytest -k slow tests/t_x.py -x\n"
        "diff --git a/tests/t_x.py b/tests/t_x.py\n--- /dev/null\n+++ b/tests/t_x.py\n"
        "@@ -0,0 +1 @@\n+def test_a(): pass\n")
    task = _task(repo, run="bash /tests/test.sh", inject=((str(held), "/tests"),))
    assert collected_paths(task) == {"tests", "tests/t_x.py"}
