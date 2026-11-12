"""Fixture-driven: the probe fires on a world-readable graded dir and stays silent on 700."""

import os
from pathlib import Path

from rlgate.core.task import GraderSpec, Task
from rlgate.pipeline import audit


def _task(tmp_path: Path, mode: int) -> Task:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "held_out.py").write_text("# secret\n")
    # a grader that always passes so the baseline is sane (oracle=1, nop=0 needs a gold patch;
    # here we restrict to the one probe, so baseline sanity is not gating it)
    (repo / "run.sh").write_text("exit 0\n")
    os.chmod(repo / "tests", mode)
    return Task(id=f"wr-{mode:o}", source="test", repo_dir=repo,
                grader=GraderSpec(run="bash run.sh"))


def test_fires_on_world_readable(tmp_path):
    task = _task(tmp_path, 0o755)
    # step 5 alone: this grader always passes, so step 2 would stop a full run before it
    result = audit(task, k=1, only={"forgery.world_readable_tests"}, steps={5})
    assert any(f.probe_id == "forgery.world_readable_tests" for f in result.findings)


def test_silent_on_700(tmp_path):
    task = _task(tmp_path, 0o700)
    # step 5 alone: this grader always passes, so step 2 would stop a full run before it
    result = audit(task, k=1, only={"forgery.world_readable_tests"}, steps={5})
    assert not any(f.probe_id == "forgery.world_readable_tests" for f in result.findings)


def test_a_repos_own_tests_folder_is_not_the_held_out_one(tmp_path):
    # Harbor / SWE-bench bring the held-out tests in only at grading: the workspace's own
    # tests/ (Django's test suite, say) is visible by design and isn't checked
    import dataclasses
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    task = _task(tmp_path, 0o755)
    task = dataclasses.replace(task, grader=dataclasses.replace(
        task.grader, inject=((str(hidden), "/held-out-tests"),)))
    result = audit(task, k=1, only={"forgery.world_readable_tests"}, steps={5})
    assert not any(f.probe_id == "forgery.world_readable_tests" for f in result.findings)
