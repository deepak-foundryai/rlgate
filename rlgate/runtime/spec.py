"""What a task needs to be STOOD UP and GRADED — the runtime counterpart to ingest.

Ingest answers "what is this task" (→ a ``Task``). This answers "how do we run it". Like
ingest, a ``RuntimeSpec`` is resolved declaratively when a preset/mapping provides one
(``Task.runtime``) and detected structurally otherwise — and it never branches on a benchmark
name, only on the *shape* of the environment (a compose file is a compose file whether the
task came from Terminal-Bench, Harbor, or a hand-rolled directory).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rlgate.core.task import Capability, Task


class RuntimeKind(Enum):
    REPO = "repo"                          # no container: grader runs on a local checkout
    SINGLE_CONTAINER = "single_container"  # one image/Dockerfile; apply patch, run grader
    COMPOSE = "compose"                    # a multi-service stack the task ships (compose)


_COMPOSE_NAMES = frozenset(
    {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"})


@dataclass(frozen=True)
class RuntimeSpec:
    """How to materialize + grade a task. Everything beyond ``kind`` is an optional hint the
    adapters and the precheck consume; an unknown field stays at its empty default."""

    kind: RuntimeKind
    compose_files: tuple[str, ...] = ()       # repo-relative, in bring-up order
    services: int = 0                         # number of compose services (a hint)
    privileged: bool = False                  # a service needs --privileged (k3s/dind/…)
    grader_entrypoint: str | None = None      # e.g. "tests/test.sh" (else task.grader.run)
    oracle: str | None = None                 # e.g. "solution/solve.sh"
    resources: dict = field(default_factory=dict)  # cpus / memory_mb / storage_mb / timeout_sec
    detail: str = ""                          # one-line human summary for the precheck

    @property
    def needs_container_host(self) -> bool:
        return self.kind in (RuntimeKind.SINGLE_CONTAINER, RuntimeKind.COMPOSE)


def detect_runtime(task: Task) -> RuntimeSpec:
    """Generic structural detection. A declarative ``task.runtime`` (from a preset/mapping)
    always wins; otherwise we read the environment's shape."""
    if getattr(task, "runtime", None) is not None:
        return task.runtime  # type: ignore[return-value]

    if task.staged:                         # environments declared by the loader
        envs = [e for e in (task.workspace_env, task.grader.env) if e is not None]
        stacks = [e.compose for e in envs if e.compose is not None]
        where = "separate grader" if task.grader.env is not None else "grader in workspace"
        if stacks:
            services, privileged = _scan_compose(list(dict.fromkeys(stacks)))
            detail = f"compose stack(s), {services} service(s), {where}"
            if privileged:
                detail += ", privileged"
            return RuntimeSpec(RuntimeKind.COMPOSE, compose_files=tuple(
                str(c) for c in dict.fromkeys(stacks)), services=services,
                privileged=privileged, detail=detail)
        return RuntimeSpec(RuntimeKind.SINGLE_CONTAINER,
                           detail=f"workspace in an image, {where}")

    compose = _find_compose(task.repo_dir)
    if compose:
        services, privileged = _scan_compose(compose)
        rel = tuple(str(c.relative_to(task.repo_dir)) for c in compose)
        detail = f"{len(rel)} compose file(s), {services} service(s)"
        if privileged:
            detail += ", privileged"
        return RuntimeSpec(RuntimeKind.COMPOSE, compose_files=rel, services=services,
                           privileged=privileged, detail=detail)

    if Capability.CONTAINER in task.capabilities:
        return RuntimeSpec(RuntimeKind.SINGLE_CONTAINER,
                           detail="single container (image/Dockerfile grader)")
    return RuntimeSpec(RuntimeKind.REPO, detail="local checkout, grader runs on the host")


def _find_compose(root: Path, max_depth: int = 2) -> list[Path]:
    """Bounded search for compose files (they live at the task root or one level down, e.g.
    environment/ and tests/). Bounded so we never walk a giant workspace (MiMo image dumps)."""
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_file() and e.name in _COMPOSE_NAMES:
                found.append(e)
            elif e.is_dir() and not e.name.startswith((".", "_")):
                walk(e, depth + 1)

    walk(Path(root), 0)
    return found


def _scan_compose(files: list[Path]) -> tuple[int, bool]:
    """Representative service count + whether any service is privileged. yaml when available,
    a cheap textual fallback otherwise (never a hard dependency just to precheck)."""
    services, privileged = 0, False
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if "privileged: true" in text or "privileged:true" in text:
            privileged = True
        services = max(services, _count_services(text))
    return services, privileged


def _count_services(text: str) -> int:
    try:
        import yaml
        doc = yaml.safe_load(text) or {}
        svc = doc.get("services")
        if isinstance(svc, dict):
            return len(svc)
        return 0
    except Exception:  # noqa: BLE001 - malformed/unavailable yaml → textual fallback
        import re
        m = re.search(r"(?m)^services:\s*$", text)
        if not m:
            return 0
        after = text[m.end():]
        count = 0
        for line in after.splitlines():
            if line.strip() and not line.startswith((" ", "\t")):
                break  # dedent → left the services block
            if re.match(r"^  (?!\s)[A-Za-z0-9_.-]+:\s*$", line):
                count += 1
        return count
