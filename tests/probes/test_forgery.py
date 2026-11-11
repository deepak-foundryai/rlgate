"""Forgery family: two deterministic detectors (forgeable_report, unrestored_test_infra) and
the live conftest-injection confirmation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Patch, ReportSpec, Task
from rlgate.ingest import spec
from rlgate.pipeline import audit
from rlgate.probes.forgery.forgeable_report import ForgeableReport
from rlgate.probes.forgery.unrestored_test_infra import UnrestoredTestInfra

GOLD = Patch(
    "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1,1 +1,1 @@\n-x = 0\n+x = 1\n"
)


def _task(tmp: Path, report: ReportSpec | None = None) -> Task:
    return Task(
        id="t", source="fixture", repo_dir=tmp,
        gold_patch=GOLD,
        grader=GraderSpec(run="grade", report=report),
    )


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def test_forgeable_report_flags_relative_path(tmp_path):
    rel = ForgeableReport().run(_task(tmp_path, ReportSpec("junit", "results.xml")), _ctx())
    assert len(rel) == 1 and rel[0].verdict is VerdictTier.REVIEW
    absolute = ForgeableReport().run(_task(tmp_path, ReportSpec("junit", "/out/r.xml")), _ctx())
    assert absolute == []


def test_unrestored_test_infra_flags_untouched_conftest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("# hooks\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n")  # the file the gold patch touches

    findings = UnrestoredTestInfra().run(_task(tmp_path), _ctx())
    assert len(findings) == 1
    assert findings[0].location == "tests/conftest.py"
    assert findings[0].verdict is VerdictTier.REVIEW


def test_unrestored_skips_reference_touched_and_pruned(tmp_path):
    # conftest.py the gold patch DOES touch is in-scope (not hidden); pruned dirs are ignored.
    (tmp_path / "conftest.py").write_text("# noop\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "sitecustomize.py").write_text("# vendored\n")
    gold = Patch("--- a/conftest.py\n+++ b/conftest.py\n@@ -1,1 +1,1 @@\n-# noop\n+# real\n")
    task = Task(id="t", source="f", repo_dir=tmp_path, gold_patch=gold,
                grader=GraderSpec(run="grade"))
    assert UnrestoredTestInfra().run(task, _ctx()) == []


def _pytest_available() -> bool:
    try:
        return subprocess.run(["python3", "-m", "pytest", "--version"],
                              capture_output=True, check=False).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _pytest_available(), reason="python3 -m pytest unavailable")
def test_conftest_injection_confirms_forgery_live():
    task = spec.load("tests/fixtures/forgery/conftest_hijack")
    result = audit(task, k=1, only={"forgery.conftest_injection"}, backend="local")

    conf = [f for f in result.findings if f.probe_id == "forgery.conftest_injection"]
    assert len(conf) == 1, [f.title for f in result.findings]
    assert conf[0].verdict is VerdictTier.CONFIRMED
    assert result.baseline_sane  # gold passes, empty fails → a forced pass is real forgery
