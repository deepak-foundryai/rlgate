"""Backend selection. `auto` uses Docker when the task declares a container image and Docker
is available, otherwise the local sandbox. Explicit `local`/`docker` override the choice."""

from __future__ import annotations

from rlgate.core.task import Capability, Task
from rlgate.sandbox.base import Sandbox


def build_sandbox(task: Task, prefer: str = "auto") -> Sandbox:
    from rlgate.sandbox.docker import DockerSandbox, available
    from rlgate.sandbox.local import LocalSandbox

    if prefer == "local":
        return LocalSandbox(task.repo_dir)
    if prefer == "docker":
        return DockerSandbox.from_task(task)

    # auto
    if Capability.CONTAINER in task.capabilities and available():
        return DockerSandbox.from_task(task)
    return LocalSandbox(task.repo_dir)
