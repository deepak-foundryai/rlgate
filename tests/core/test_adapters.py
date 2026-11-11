"""The execution-adapter registry + resolution: backends are adapters, new kinds plug in."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlgate.core.task import GraderSpec, Task
from rlgate.runtime import HostCapabilities
from rlgate.runtime.adapter import AdapterUnavailable, adapters, resolve_adapter


def _task(tmp_path: Path, *, image: str | None = None) -> Task:
    (tmp_path / "repo").mkdir(exist_ok=True)
    return Task(id="t", source="test", repo_dir=tmp_path / "repo",
                grader=GraderSpec(run="true", image=image))


def test_builtin_backends_register():
    names = set(adapters())
    assert {"local", "docker", "daytona"} <= names


def test_resolve_repo_task_is_local(tmp_path):
    a = resolve_adapter(_task(tmp_path), host=HostCapabilities())
    assert a.name == "local"


def test_resolve_single_container_prefers_daytona(tmp_path):
    a = resolve_adapter(_task(tmp_path, image="img:latest"),
                        host=HostCapabilities(daytona=True))
    assert a.name == "daytona"


def test_prefer_override_forces_backend(tmp_path):
    a = resolve_adapter(_task(tmp_path), prefer="local", host=HostCapabilities())
    assert a.name == "local"


def test_compose_task_without_adapter_is_a_clear_error(tmp_path):
    t = _task(tmp_path)
    (t.repo_dir / "docker-compose.yml").write_text("services:\n  a: {image: x}\n  b: {image: y}\n")
    with pytest.raises(AdapterUnavailable, match="compose"):
        resolve_adapter(t, host=HostCapabilities(docker=True, compose=True))


def test_local_adapter_provisions_and_execs(tmp_path):
    a = resolve_adapter(_task(tmp_path), host=HostCapabilities())
    a.provision()
    with a.overlay() as ov:
        assert ov.exec("echo hi").stdout.strip() == "hi"
