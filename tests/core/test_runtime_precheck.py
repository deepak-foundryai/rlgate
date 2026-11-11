"""Runtime detection + precheck: generic across env shapes, never benchmark-aware."""

from __future__ import annotations

from pathlib import Path

from rlgate.cli import main
from rlgate.core.task import GraderSpec, Task
from rlgate.runtime import HostCapabilities, RuntimeKind, detect_runtime, precheck


def _task(tmp_path: Path, *, image: str | None = None) -> Task:
    (tmp_path / "repo").mkdir(exist_ok=True)
    return Task(id="t", source="test", repo_dir=tmp_path / "repo",
                grader=GraderSpec(run="true", image=image))


COMPOSE = """\
services:
  main:
    build: .
  k3s:
    image: rancher/k3s:v1.31.4-k3s1
    privileged: true
  prometheus:
    image: prom/prometheus
"""


def test_detect_compose_with_privileged(tmp_path):
    t = _task(tmp_path)
    (t.repo_dir / "environment").mkdir()
    (t.repo_dir / "environment" / "docker-compose.yaml").write_text(COMPOSE)
    spec = detect_runtime(t)
    assert spec.kind is RuntimeKind.COMPOSE
    assert spec.services == 3 and spec.privileged is True
    assert spec.compose_files == ("environment/docker-compose.yaml",)


def test_detect_single_container(tmp_path):
    spec = detect_runtime(_task(tmp_path, image="repo:latest"))
    assert spec.kind is RuntimeKind.SINGLE_CONTAINER


def test_detect_repo(tmp_path):
    spec = detect_runtime(_task(tmp_path))
    assert spec.kind is RuntimeKind.REPO


def test_precheck_compose_blocked_without_docker(tmp_path):
    t = _task(tmp_path)
    (t.repo_dir / "docker-compose.yml").write_text(COMPOSE)
    host = HostCapabilities(docker=False, compose=False, daytona=True)
    plan = precheck(t, host)
    assert plan.kind is RuntimeKind.COMPOSE and not plan.usable
    assert "Docker" in plan.remediation and "privileged" in plan.remediation


def test_precheck_compose_ok_with_docker_and_compose(tmp_path):
    t = _task(tmp_path)
    (t.repo_dir / "docker-compose.yml").write_text(COMPOSE)
    plan = precheck(t, HostCapabilities(docker=True, compose=True, daytona=False))
    assert plan.usable and plan.adapter == "compose"


def test_precheck_single_container_prefers_daytona_then_docker(tmp_path):
    t = _task(tmp_path, image="repo:latest")
    assert precheck(t, HostCapabilities(daytona=True)).adapter == "daytona"
    assert precheck(t, HostCapabilities(docker=True)).adapter == "docker"
    blocked = precheck(t, HostCapabilities())
    assert not blocked.usable and "backend" in blocked.remediation


def test_precheck_repo_always_runnable(tmp_path):
    plan = precheck(_task(tmp_path), HostCapabilities())
    assert plan.usable and plan.adapter == "local"


def test_doctor_shows_how_each_task_would_run(capsys):
    rc = main(["doctor", "examples/taskbank"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "How each task would run" in out and "weak_grader" in out and "full run" in out
