"""Precheck: before standing a task up, decide HOW it would run and whether this host CAN.

Generalizes the single-backend preflight (``sandbox.select.resolve_backend``) to every
environment kind. Produces one ``RuntimePlan`` per task — the detected runtime, the adapter
that would execute it, and a go/no-go with concrete remediation — so *any* task type gives an
honest answer up front instead of failing deep in a build or a grader.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from rlgate.core.task import Task
from rlgate.runtime.spec import RuntimeKind, RuntimeSpec, detect_runtime


@dataclass
class HostCapabilities:
    """What this host can offer an execution adapter. Probed once, reused across a bank."""

    docker: bool = False
    compose: bool = False
    daytona: bool = False


def probe_host() -> HostCapabilities:
    from rlgate.sandbox.daytona import unavailable_reason as daytona_reason
    from rlgate.sandbox.docker import unavailable_reason as docker_reason

    docker = docker_reason() is None
    return HostCapabilities(
        docker=docker,
        compose=_compose_ok() if docker else False,
        daytona=daytona_reason() is None,
    )


def _compose_ok() -> bool:
    try:
        return subprocess.run(["docker", "compose", "version"],
                              capture_output=True, check=False).returncode == 0
    except (FileNotFoundError, OSError):
        return False


@dataclass
class RuntimePlan:
    task_id: str
    spec: RuntimeSpec
    adapter: str                     # the adapter that would run it (e.g. "compose", "daytona")
    usable: bool
    reason: str                      # why this adapter / this verdict
    remediation: str | None = None   # how to make it usable, when it isn't

    @property
    def kind(self) -> RuntimeKind:
        return self.spec.kind


def precheck(task: Task, host: HostCapabilities | None = None) -> RuntimePlan:
    host = host or probe_host()
    spec = detect_runtime(task)

    if spec.kind is RuntimeKind.REPO:
        return RuntimePlan(task.id, spec, "local", True,
                           "local checkout — grader runs on the host, no container")

    if spec.kind is RuntimeKind.SINGLE_CONTAINER and not task.meta.get("unsupported"):
        if host.daytona:
            return RuntimePlan(task.id, spec, "daytona", True,
                               "single-container task — Daytona is configured")
        if host.docker:
            return RuntimePlan(task.id, spec, "docker", True,
                               "single-container task — Docker daemon is present")
        return RuntimePlan(task.id, spec, "docker", False,
                           "single-container task needs a container backend",
                           "configure Daytona (DAYTONA_API_KEY in .env) or start Docker — "
                           "or use --backend local (grader may need its image)")

    unsupported = task.meta.get("unsupported") if isinstance(task.meta, dict) else None
    if unsupported:
        return RuntimePlan(task.id, spec, "none", False, "; ".join(unsupported),
                           "static checks still apply (--static)")

    # COMPOSE — a stack the task format declares runs on the staged backend (Daytona
    # Docker-in-Docker, or this host's Docker + Compose)
    if task.staged and host.daytona:
        return RuntimePlan(task.id, spec, "daytona", True, _compose_reason(spec)
                           + " — runs in a Docker-in-Docker Daytona sandbox" + _priv_note(spec))
    if not host.docker:
        return RuntimePlan(task.id, spec, "compose", False,
                           _compose_reason(spec),
                           "needs Docker + the Compose plugin on the host" + _priv(spec)
                           + ("; or configure Daytona (DAYTONA_API_KEY in .env)"
                              if task.staged else "; start Docker here, or run rlgate on a "
                              "Docker host"))
    if not host.compose:
        return RuntimePlan(task.id, spec, "compose", False,
                           _compose_reason(spec),
                           "Docker is present but 'docker compose' is missing — install "
                           "the Compose v2 plugin")
    return RuntimePlan(task.id, spec, "docker" if task.staged else "compose", True,
                       _compose_reason(spec) + " — Docker + Compose present"
                       + _priv_note(spec))


def _compose_reason(spec: RuntimeSpec) -> str:
    return spec.detail or f"multi-service compose stack ({spec.services} service(s))"


def _priv(spec: RuntimeSpec) -> str:
    return " (privileged, for stacks with k3s/dind)" if spec.privileged else ""


def _priv_note(spec: RuntimeSpec) -> str:
    return " — stack needs privileged; ensure the host allows it" if spec.privileged else ""
