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
    # a broad COPY is reported only when the context holds held-out material (none here)
    assert "broad COPY" not in titles
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


def _built_from(tmp_path: Path, dockerfile: str, *, held: dict[str, str]) -> Task:
    """A task whose agent image is built from ``env/Dockerfile``, with its reference
    solution in ``solution/``."""
    from rlgate.core.task import EnvSpec
    env = tmp_path / "env"
    env.mkdir()
    (env / "Dockerfile").write_text(dockerfile)
    (env / "app.py").write_text("print('the starting code the agent works on')\n" * 3)
    sol = tmp_path / "solution"
    sol.mkdir()
    for name, text in held.items():
        (sol / name).write_text(text)
    return Task(id="t", source="fixture", repo_dir=env, grader=GraderSpec(run="grade"),
                workspace_env=EnvSpec(dockerfile=env / "Dockerfile"),
                oracle=(str(sol), "solve.sh"))


REFERENCE = "def solve():\n    return 'the reference answer, long enough to count'\n" * 2


def test_broad_copy_is_flagged_when_the_context_holds_a_copy_of_the_reference(tmp_path):
    task = _built_from(tmp_path, "FROM python:3.12\nCOPY . /app\n",
                       held={"answer.py": REFERENCE})
    (tmp_path / "env" / "helpers.py").write_text(REFERENCE)
    [f] = [f for f in DockerfileHygiene().run(task, _ctx()) if f.rule_id == "RG601"]
    assert "helpers.py, a copy of the held-out solution/answer.py" in f.evidence.detail


def test_broad_copy_of_a_workspace_only_context_is_not_flagged(tmp_path):
    task = _built_from(tmp_path, "FROM python:3.12\nCOPY . /app\n",
                       held={"answer.py": REFERENCE})
    assert not [f for f in DockerfileHygiene().run(task, _ctx()) if f.rule_id == "RG601"]


def test_python_inside_a_run_line_is_not_a_from_instruction(tmp_path):
    task = _built_from(tmp_path, "FROM python:3.12\n"
                       "RUN python -c 'import os; \\\n"
                       "from huggingface_hub import snapshot_download'\n"
                       "RUN <<EOF\nfrom x import y\nEOF\n", held={})
    assert not [f for f in DockerfileHygiene().run(task, _ctx()) if f.rule_id == "RG602"]


def test_the_recipe_of_a_prebuilt_image_is_not_linted(tmp_path):
    from rlgate.core.task import EnvSpec
    (tmp_path / "Dockerfile").write_text("FROM python\nRUN make -j$(nproc)\n")
    task = Task(id="t", source="fixture", repo_dir=tmp_path,
                grader=GraderSpec(run="grade", image=str(tmp_path)),
                workspace_env=EnvSpec(image="registry.example/task@sha256:" + "0" * 64))
    assert DockerfileHygiene().run(task, _ctx()) == []
