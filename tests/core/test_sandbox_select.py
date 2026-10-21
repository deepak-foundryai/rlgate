"""Backend selection is capability-driven (no benchmark identity)."""

from pathlib import Path

from rlgate.core.task import GraderSpec, Task
from rlgate.sandbox.docker import DockerSandbox
from rlgate.sandbox.local import LocalSandbox
from rlgate.sandbox.select import build_sandbox


def _task(tmp_path: Path, image: str | None) -> Task:
    (tmp_path / "repo").mkdir()
    return Task(id="t", source="test", repo_dir=tmp_path / "repo",
                grader=GraderSpec(run="true", image=image))


def test_explicit_local(tmp_path):
    assert isinstance(build_sandbox(_task(tmp_path, None), prefer="local"), LocalSandbox)


def test_explicit_docker(tmp_path):
    # does not require docker to be running — only constructs the object
    assert isinstance(build_sandbox(_task(tmp_path, "img:latest"), prefer="docker"),
                      DockerSandbox)


def test_explicit_daytona(tmp_path):
    # construct-only; does not require keys or the SDK (build() would)
    from rlgate.sandbox.daytona import DaytonaSandbox
    assert isinstance(build_sandbox(_task(tmp_path, "img:latest"), prefer="daytona"),
                      DaytonaSandbox)


def test_auto_without_image_is_local(tmp_path):
    # no container capability → local, regardless of docker availability
    assert isinstance(build_sandbox(_task(tmp_path, None), prefer="auto"), LocalSandbox)
