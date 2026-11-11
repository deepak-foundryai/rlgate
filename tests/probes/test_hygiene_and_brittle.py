"""§3.2 Dockerfile hygiene + §3.9 brittle-assertion static detectors."""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Patch, Task
from rlgate.probes.hygiene.dockerfile import DockerfileHygiene
from rlgate.probes.test_quality.brittle_assertions import BrittleAssertions

HYG = Path("tests/fixtures/hygiene")


def _ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(1.0, 0.0, 1), variants=None)


def _task_img(dockerfile: str) -> Task:
    return Task(id="t", source="fixture", repo_dir=HYG,
                grader=GraderSpec(run="grade", image=str(HYG / dockerfile)))


def test_dockerfile_hygiene_flags_each_issue():
    findings = DockerfileHygiene().run(_task_img("Dockerfile.dirty"), _ctx())
    titles = " ".join(f.title for f in findings)
    assert "broad COPY" in titles
    assert "unpinned base image" in titles
    assert "nproc" in titles
    assert "unpinned pip" in titles
    assert all(f.verdict is VerdictTier.REVIEW for f in findings)


def test_dockerfile_hygiene_clean_is_quiet():
    assert DockerfileHygiene().run(_task_img("Dockerfile.clean"), _ctx()) == []


def test_dockerfile_hygiene_noop_on_registry_ref():
    task = Task(id="t", source="f", repo_dir=HYG,
                grader=GraderSpec(run="grade", image="python:3.12-slim"))  # not a file
    assert DockerfileHygiene().run(task, _ctx()) == []


GOLD = Patch(
    '--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,2 @@\n-x = 0\n'
    '+def run():\n+    raise ValueError("boom happened")\n'
)


def _task_tests(tmp: Path, instruction: str = "Implement run().") -> Task:
    return Task(id="t", source="fixture", repo_dir=tmp, gold_patch=GOLD,
                instruction=instruction, grader=GraderSpec(run="grade"))


def test_brittle_exact_string_and_call_count(tmp_path):
    (tmp_path / "test_x.py").write_text(
        'import pytest, mock\n'
        'def test_msg():\n'
        '    with pytest.raises(ValueError, match="boom happened"):\n'   # pins gold string
        '        run()\n'
        'def test_calls(m):\n'
        '    m.assert_called_once()\n'                                   # call-count
    )
    by_kind = {f.title.split(":")[0] for f in BrittleAssertions().run(_task_tests(tmp_path), _ctx())}
    assert "exact-string pinning" in by_kind
    assert "call-count assertion" in by_kind


def test_brittle_quiet_when_behavioral(tmp_path):
    (tmp_path / "test_x.py").write_text(
        "def test_behavior():\n    assert run() is None\n"
    )
    assert BrittleAssertions().run(_task_tests(tmp_path), _ctx()) == []


def test_brittle_string_skipped_when_instruction_names_it(tmp_path):
    (tmp_path / "test_x.py").write_text(
        'import pytest\n'
        'def test_msg():\n'
        '    with pytest.raises(ValueError, match="boom happened"):\n'
        '        run()\n'
    )
    task = _task_tests(tmp_path, instruction="run() must raise ValueError('boom happened').")
    assert BrittleAssertions().run(task, _ctx()) == []
