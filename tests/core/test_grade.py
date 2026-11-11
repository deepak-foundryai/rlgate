"""One grading function: baseline, variant engine and payload probes agree on what a pass is."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rlgate.baseline.invariant import establish
from rlgate.core.grade import _passes, run_grader
from rlgate.core.task import GraderSpec, Patch, ReportSpec, Task
from rlgate.ingest import spec
from rlgate.sandbox.local import LocalSandbox
from rlgate.sandbox.parsers import TestCase, TestReport, TestStatus
from rlgate.variants.engine import VariantEngine
from rlgate.variants.revert import Variant

WEAK = "tests/fixtures/grader_strength/weak_suite"


def _report(*statuses: TestStatus) -> TestReport:
    return TestReport([TestCase(f"t{i}", s) for i, s in enumerate(statuses)])


@pytest.mark.parametrize("mode, exit_code, report, want", [
    ("exit_code", 0, None, True),
    ("exit_code", 1, _report(TestStatus.PASS), False),
    ("exit_code", 0, _report(TestStatus.FAIL), True),      # exit code is all it trusts
    ("report", 0, _report(TestStatus.FAIL), False),
    ("report", 1, _report(TestStatus.PASS), True),
    ("report", 0, None, False),                            # no report → no pass
    ("report", 0, _report(TestStatus.SKIP), False),        # nothing passed → no pass
    ("both", 0, _report(TestStatus.PASS), True),
    ("both", 1, _report(TestStatus.PASS), False),
    ("both", 0, _report(TestStatus.PASS, TestStatus.FAIL), False),
])
def test_verdict_modes(mode, exit_code, report, want):
    assert _passes(mode, exit_code, report) is want


def test_unknown_verdict_mode_is_rejected():
    with pytest.raises(ValueError, match="grader.verdict"):
        _passes("vibes", 0, None)


def test_baseline_engine_and_direct_grade_agree_on_weak_suite():
    task = spec.load(WEAK)
    sb = LocalSandbox(task.repo_dir)
    sb.build()
    baseline = establish(task, sb, k=1)

    with sb.overlay(task.gold_patch.text) as ovl:
        gold = run_grader(ovl, task)
    with sb.overlay(None) as ovl:
        nop = run_grader(ovl, task)
    assert (baseline.oracle, baseline.nop) == (gold.reward, nop.reward) == (1.0, 0.0)

    # The engine scores the gold patch itself as a "variant" through the same function.
    engine = VariantEngine(task, sb, baseline)
    as_variant = engine._score(Variant("gold", "identity", task.gold_patch.text, "gold"))
    assert as_variant.reward == gold.reward


def _junit_task(tmp_path: Path, verdict: str) -> Task:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_x.py").write_text(textwrap.dedent("""
        def test_ok():
            assert True

        def test_broken():
            assert False
    """))
    # `; exit 0` masks the failing test in the exit code — only the report shows it.
    return Task(
        id="junit", source="test", repo_dir=repo,
        grader=GraderSpec(
            run="python3 -m pytest -q -p no:cacheprovider --junitxml=out.xml; exit 0",
            report=ReportSpec("junit", "out.xml"), verdict=verdict,
        ),
        gold_patch=Patch(""),
    )


@pytest.mark.parametrize("verdict, want", [("exit_code", True), ("report", False),
                                           ("both", False)])
def test_verdict_mode_decides_a_masked_failure(tmp_path, verdict, want):
    task = _junit_task(tmp_path, verdict)
    sb = LocalSandbox(task.repo_dir)
    sb.build()
    with sb.overlay(None) as ovl:
        g = run_grader(ovl, task)
    assert g.exit_code == 0
    assert g.report is not None and g.report.failed
    assert g.passed is want


def test_one_command_grades_and_returns_the_reward_file(tmp_path):
    import subprocess as sp
    from types import SimpleNamespace

    from rlgate.core.grade import run_grader
    from rlgate.core.task import GraderSpec, Task

    seen: list[str] = []

    class Ovl:
        def before_grade(self):
            pass

        def exec(self, cmd, timeout=60):
            seen.append(cmd)
            p = sp.run(["sh", "-c", cmd], cwd=tmp_path, capture_output=True, text=True,
                       check=False)
            return SimpleNamespace(exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)

    (tmp_path / "old.txt").write_text("1\n")                       # stale: must be cleared
    grader = GraderSpec(run="echo '@@rlgate-fake@@ RC 0'; echo 0.5 > r.txt; exit 3",
                        verdict="reward_file", reward_files=("old.txt", "r.txt"))
    task = Task(id="t", source="fixture", repo_dir=tmp_path, grader=grader)
    g = run_grader(Ovl(), task)  # type: ignore[arg-type]
    assert (g.reward, g.exit_code) == (0.5, 3) and len(seen) == 1   # one round trip
    assert "@@rlgate-fake@@" in g.stdout and "@@rlgate-" not in g.stdout.replace(
        "@@rlgate-fake@@", "")                                    # the frame is stripped
