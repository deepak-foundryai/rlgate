"""B-series: equivalence over-fit (CONFIRMED, live) and symbol cross-ref (REVIEW, static)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Patch, Task
from rlgate.ingest import spec
from rlgate.pipeline import audit
from rlgate.probes.test_quality.symbol_crossref import SymbolCrossref

GOLD = Patch(
    "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,2 @@\n-x = 0\n"
    "+def _coalesce_buffer(items):\n+    return items\n"
)


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def _task(tmp: Path, instruction: str) -> Task:
    return Task(id="t", source="fixture", repo_dir=tmp, gold_patch=GOLD,
                instruction=instruction, grader=GraderSpec(run="grade"))


def test_symbol_crossref_flags_internal_reference(tmp_path):
    (tmp_path / "test_x.py").write_text("from mod import coalesce\n\n"
                                        "def test_it():\n    assert _coalesce_buffer([1]) == [1]\n")
    findings = SymbolCrossref().run(_task(tmp_path, "Implement coalesce()."), _ctx())
    assert len(findings) == 1
    assert findings[0].verdict is VerdictTier.REVIEW
    assert "_coalesce_buffer" in findings[0].title
    assert findings[0].location == "test_x.py"


def test_symbol_crossref_quiet_when_only_public_api_referenced(tmp_path):
    (tmp_path / "test_x.py").write_text("from mod import coalesce\n\n"
                                        "def test_it():\n    assert coalesce([1]) == [1]\n")
    assert SymbolCrossref().run(_task(tmp_path, "Implement coalesce()."), _ctx()) == []


def _pytest_available() -> bool:
    try:
        return subprocess.run(["python3", "-m", "pytest", "--version"],
                              capture_output=True, check=False).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _pytest_available(), reason="python3 -m pytest unavailable")
def test_overfit_equivalence_confirms_message_overfit_live():
    task = spec.load("tests/fixtures/test_quality/overfit_message")
    result = audit(task, k=1, only={"test_quality.overfit_equivalence"}, backend="local")

    over = [f for f in result.findings if f.probe_id == "test_quality.overfit_equivalence"]
    assert len(over) == 1, [f.title for f in result.findings]
    assert over[0].verdict is VerdictTier.CONFIRMED
    assert result.baseline_sane
