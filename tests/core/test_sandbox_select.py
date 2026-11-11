"""Backend selection is capability-driven, and an unusable explicit backend fails loudly."""

from pathlib import Path

import pytest

from rlgate.core.task import GraderSpec, Task
from rlgate.sandbox import select
from rlgate.sandbox.local import LocalSandbox
from rlgate.sandbox.select import BackendUnavailable, build_sandbox, resolve_backend


def _task(tmp_path: Path, image: str | None) -> Task:
    (tmp_path / "repo").mkdir()
    return Task(id="t", source="test", repo_dir=tmp_path / "repo",
                grader=GraderSpec(run="true", image=image))


def test_explicit_local(tmp_path):
    assert isinstance(build_sandbox(_task(tmp_path, None), prefer="local"), LocalSandbox)


def test_auto_without_image_is_local(tmp_path):
    assert isinstance(build_sandbox(_task(tmp_path, None), prefer="auto"), LocalSandbox)


def test_explicit_docker_builds_when_available(tmp_path, monkeypatch):
    from rlgate.sandbox.docker import DockerSandbox
    monkeypatch.setattr(select, "_docker_reason", lambda: None)
    assert isinstance(build_sandbox(_task(tmp_path, "img:latest"), prefer="docker"), DockerSandbox)


def test_explicit_backend_unavailable_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(select, "_docker_reason", lambda: "the Docker daemon is not running")
    with pytest.raises(BackendUnavailable, match="Docker daemon"):
        build_sandbox(_task(tmp_path, "img:latest"), prefer="docker")


def test_auto_container_prefers_daytona_then_docker(tmp_path, monkeypatch):
    task = _task(tmp_path, "img:latest")
    monkeypatch.setattr(select, "_daytona_reason", lambda: None)
    assert resolve_backend(task, "auto").name == "daytona"
    monkeypatch.setattr(select, "_daytona_reason", lambda: "no key")
    monkeypatch.setattr(select, "_docker_reason", lambda: None)
    assert resolve_backend(task, "auto").name == "docker"


def test_auto_container_falls_back_to_local_with_reason(tmp_path, monkeypatch):
    task = _task(tmp_path, "img:latest")
    monkeypatch.setattr(select, "_daytona_reason", lambda: "no key")
    monkeypatch.setattr(select, "_docker_reason", lambda: "no daemon")
    choice = resolve_backend(task, "auto")
    assert choice.name == "local" and choice.usable and "falling back to local" in choice.reason


def test_explicit_daytona_reports_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr(select, "_daytona_reason", lambda: "DAYTONA_API_KEY is not set")
    choice = resolve_backend(_task(tmp_path, "img:latest"), "daytona")
    assert not choice.usable and "DAYTONA_API_KEY" in choice.unavailable
