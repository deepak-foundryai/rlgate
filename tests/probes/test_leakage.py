"""Leakage detectors run on the host repo_dir: mtime clustering and on-disk answer artifacts."""

from __future__ import annotations

import os
from pathlib import Path

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Task
from rlgate.probes.leakage.cache_scan import CacheScan
from rlgate.probes.leakage.mtime_clustering import MtimeClustering


def _task(tmp: Path) -> Task:
    return Task(id="t", source="fixture", repo_dir=tmp, grader=GraderSpec(run="grade"))


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def _touch(p: Path, mtime: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n")
    os.utime(p, (mtime, mtime))


def test_mtime_clustering_flags_the_late_minority(tmp_path):
    base = 1_700_000_000
    for i in range(10):
        _touch(tmp_path / f"src/f{i}.py", base)       # checkout bulk
    _touch(tmp_path / "held/answer_a.py", base + 50_000)   # injected later
    _touch(tmp_path / "held/answer_b.py", base + 50_000)

    findings = MtimeClustering().run(_task(tmp_path), _ctx())
    assert len(findings) == 1 and findings[0].verdict is VerdictTier.REVIEW
    assert "2 file(s)" in findings[0].title


def test_mtime_clustering_quiet_when_uniform(tmp_path):
    base = 1_700_000_000
    for i in range(12):
        _touch(tmp_path / f"f{i}.py", base)
    assert MtimeClustering().run(_task(tmp_path), _ctx()) == []


def test_mtime_clustering_quiet_when_majority_is_late(tmp_path):
    # Most files newer than a tiny old minority is NOT the injected-answer shape.
    base = 1_700_000_000
    for i in range(2):
        _touch(tmp_path / f"old{i}.py", base)
    for i in range(10):
        _touch(tmp_path / f"new{i}.py", base + 50_000)
    assert MtimeClustering().run(_task(tmp_path), _ctx()) == []


def test_cache_scan_orphan_bytecode_and_leftovers_and_answers(tmp_path):
    # orphan .pyc (no source) -> CONFIRMED; a .pyc WITH source -> ignored
    (tmp_path / "pkg/__pycache__").mkdir(parents=True)
    (tmp_path / "pkg/__pycache__/secret.cpython-312.pyc").write_bytes(b"\x00bytecode")
    (tmp_path / "pkg/__pycache__/present.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / "pkg/present.py").write_text("ok\n")
    (tmp_path / "fix.py.orig").write_text("the old impl\n")       # leftover -> CONFIRMED
    (tmp_path / "solution.py").write_text("return 42\n")          # answer-named -> REVIEW

    by_loc = {f.location: f for f in CacheScan().run(_task(tmp_path), _ctx())}
    assert "pkg/__pycache__/secret.cpython-312.pyc" in by_loc
    assert by_loc["pkg/__pycache__/secret.cpython-312.pyc"].verdict is VerdictTier.CONFIRMED
    assert "pkg/__pycache__/present.cpython-312.pyc" not in by_loc   # source present
    assert by_loc["fix.py.orig"].verdict is VerdictTier.CONFIRMED
    assert by_loc["solution.py"].verdict is VerdictTier.REVIEW


def test_cache_scan_quiet_on_clean_tree(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1\n")
    (tmp_path / "pkg/__pycache__").mkdir(parents=True)
    (tmp_path / "pkg/mod.py").write_text("y = 2\n")
    (tmp_path / "pkg/__pycache__/mod.cpython-312.pyc").write_bytes(b"\x00")
    assert CacheScan().run(_task(tmp_path), _ctx()) == []
