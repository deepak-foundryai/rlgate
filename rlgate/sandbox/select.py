"""Backend selection (capability-driven, never benchmark identity).

`auto` for a task that needs a container prefers **Daytona** (the configured cloud backend),
then local Docker if a daemon is present, else the local sandbox. Explicit
`local`/`docker`/`daytona` override the choice — and if that backend isn't usable we fail
loudly with how to fix it, rather than crashing deep in a backend SDK or silently running the
grader somewhere it can't work.
"""

from __future__ import annotations

from dataclasses import dataclass

from rlgate.core.task import Capability, Task
from rlgate.sandbox.base import Sandbox


class BackendUnavailable(RuntimeError):
    """A requested backend can't be used; the message says why and how to fix it."""


@dataclass
class BackendChoice:
    name: str                 # "local" | "docker" | "daytona"
    reason: str               # why this backend was chosen
    unavailable: str | None   # None if usable, else why not (+ fix)

    @property
    def usable(self) -> bool:
        return self.unavailable is None


def _daytona_reason() -> str | None:
    from rlgate.sandbox.daytona import unavailable_reason
    return unavailable_reason()


def _docker_reason() -> str | None:
    from rlgate.sandbox.docker import unavailable_reason
    return unavailable_reason()


def resolve_backend(task: Task, prefer: str = "auto") -> BackendChoice:
    """Decide which backend WOULD run, and whether it's usable — without building it."""
    if prefer == "local":
        return BackendChoice("local", "requested", None)
    if prefer == "daytona":
        return BackendChoice("daytona", "requested", _daytona_reason())
    if prefer == "docker":
        return BackendChoice("docker", "requested", _docker_reason())

    # auto
    if Capability.CONTAINER in task.capabilities:
        if _daytona_reason() is None:
            return BackendChoice("daytona", "auto: container task, Daytona configured", None)
        if _docker_reason() is None:
            return BackendChoice("docker", "auto: container task, Docker daemon present", None)
        return BackendChoice(
            "local", "auto: container task but no Daytona/Docker — falling back to local "
            "(the grader likely needs its container; results may be unreliable)", None)
    return BackendChoice("local", "auto: no container needed", None)


def build_sandbox(task: Task, prefer: str = "auto") -> Sandbox:
    choice = resolve_backend(task, prefer)
    if not choice.usable:
        raise BackendUnavailable(f"backend '{choice.name}' unavailable: {choice.unavailable}")
    if choice.name == "daytona":
        from rlgate.sandbox.daytona import DaytonaSandbox
        return DaytonaSandbox.from_task(task)
    if choice.name == "docker":
        from rlgate.sandbox.docker import DockerSandbox
        return DockerSandbox.from_task(task)
    from rlgate.sandbox.local import LocalSandbox
    return LocalSandbox(task.repo_dir)
