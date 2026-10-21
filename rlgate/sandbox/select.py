"""Backend selection (capability-driven, never benchmark identity).

`auto` for a task that needs a container prefers **Daytona** (the configured cloud backend),
then local Docker if a daemon is present, else the local sandbox. Explicit
`local`/`docker`/`daytona` override the choice."""

from __future__ import annotations

from rlgate.core.task import Capability, Task
from rlgate.sandbox.base import Sandbox


def build_sandbox(task: Task, prefer: str = "auto") -> Sandbox:
    from rlgate.sandbox.local import LocalSandbox

    if prefer == "local":
        return LocalSandbox(task.repo_dir)
    if prefer == "daytona":
        from rlgate.sandbox.daytona import DaytonaSandbox
        return DaytonaSandbox.from_task(task)
    if prefer == "docker":
        from rlgate.sandbox.docker import DockerSandbox
        return DockerSandbox.from_task(task)

    # auto
    if Capability.CONTAINER in task.capabilities:
        from rlgate.sandbox.daytona import DaytonaSandbox
        from rlgate.sandbox.daytona import available as daytona_available
        if daytona_available():
            return DaytonaSandbox.from_task(task)
        from rlgate.sandbox.docker import DockerSandbox
        from rlgate.sandbox.docker import available as docker_available
        if docker_available():
            return DockerSandbox.from_task(task)
    return LocalSandbox(task.repo_dir)
