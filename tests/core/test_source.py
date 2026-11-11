"""Source-level variants: gold tree, changed-function discovery, body rewrite, diff back."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

from rlgate.core.grade import run_grader
from rlgate.core.task import GraderSpec, Patch, Task
from rlgate.ingest import spec
from rlgate.sandbox.local import LocalSandbox
from rlgate.variants.source import (
    changed_functions,
    diff_variant,
    gold_files,
    replace_body,
    unified_diff,
)

WEAK = "tests/fixtures/grader_strength/weak_suite"


def _stub(task: Task, qualname: str) -> str:
    [fc] = gold_files(task).values()
    [span] = [s for s in changed_functions(fc) if s.qualname == qualname]
    v = diff_variant(task, {fc.path: replace_body(fc.gold, span, ["raise NotImplementedError"])},
                     origin=f"{fc.path}::{qualname}", operator="stub")
    assert v is not None
    return v.patch_text


def test_changed_functions_on_weak_suite():
    task = spec.load(WEAK)
    [fc] = gold_files(task).values()
    assert fc.path == "calc.py" and fc.added == frozenset({2, 14})
    assert [s.qualname for s in changed_functions(fc)] == ["f", "g"]


def test_stub_variants_apply_and_grade():
    """Stubbing g (untested) still passes; stubbing f (tested) fails — the stub-probe premise."""
    task = spec.load(WEAK)
    sb = LocalSandbox(task.repo_dir)
    sb.build()
    with sb.overlay(_stub(task, "g")) as ovl:
        assert run_grader(ovl, task).passed
    with sb.overlay(_stub(task, "f")) as ovl:
        assert not run_grader(ovl, task).passed


def _task_from(tmp_path: Path, base: dict[str, str], gold: dict[str, str]) -> Task:
    repo = tmp_path / "repo"
    for p, text in base.items():
        (repo / p).parent.mkdir(parents=True, exist_ok=True)
        (repo / p).write_text(text)
    patch = unified_diff(repo, gold)
    return Task(id="t", source="test", repo_dir=repo, grader=GraderSpec(run="true"),
                gold_patch=Patch(patch))


def test_methods_docstrings_nesting_and_one_liners(tmp_path):
    base = textwrap.dedent('''\
        class C:
            def m(self, x):
                """Doc."""
                return x

            def untouched(self):
                return 1


        def outer(x):
            def inner(y):
                return y
            return inner(x)


        def one(x): return x
    ''')
    gold = (base.replace("        return x\n", "        return x * 2\n")
            .replace("        return y\n", "        return y + 1\n")
            .replace("def one(x): return x", "def one(x): return -x"))
    task = _task_from(tmp_path, {"pkg/mod.py": base}, {"pkg/mod.py": gold})
    [fc] = gold_files(task).values()
    spans = changed_functions(fc)
    # method kept, docstring preserved; nested def reported once (outermost); one-liner skipped
    assert [s.qualname for s in spans] == ["C.m", "outer"]
    m = spans[0]
    assert fc.gold.splitlines()[m.body_start - 1].strip() == "return x * 2"
    rewritten = replace_body(fc.gold, m, ["return None"])
    assert '"""Doc."""' in rewritten and "return x * 2" not in rewritten
    compile(rewritten, "mod.py", "exec")


def test_unified_diff_handles_missing_newline_new_files_and_modes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "run.sh").write_text("echo a")          # no trailing newline
    os.chmod(repo / "run.sh", 0o755)
    patch = unified_diff(repo, {"run.sh": "echo b", "new.py": "x = 1\n"})
    assert "No newline at end of file" in patch
    assert "new file mode" in patch and "old mode" not in patch

    sb = LocalSandbox(repo)
    sb.build()
    with sb.overlay(patch) as ovl:
        assert (ovl.root / "run.sh").read_text() == "echo b"
        assert (ovl.root / "new.py").read_text() == "x = 1\n"
        assert os.stat(ovl.root / "run.sh").st_mode & 0o111


def test_noop_rewrite_is_no_variant(tmp_path):
    task = _task_from(tmp_path, {"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    assert diff_variant(task, {"a.py": "x = 1\n"}, origin="a", operator="noop") is None
