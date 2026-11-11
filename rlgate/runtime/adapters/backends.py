"""The single-container / local backends, expressed as execution adapters.

These wrap the existing ``Sandbox`` classes unchanged — so REPO and SINGLE_CONTAINER tasks
run exactly as before, now behind the same interface a compose or staged adapter implements.
"""

from __future__ import annotations

from typing import ClassVar

from rlgate.core.task import Task
from rlgate.runtime.adapter import ExecutionAdapter, GradeCost, register_adapter
from rlgate.runtime.spec import RuntimeKind
from rlgate.sandbox.base import Overlay, Sandbox


class _SandboxBackedAdapter(ExecutionAdapter):
    """Delegates the adapter interface to a classic ``Sandbox``."""

    def __init__(self, sandbox: Sandbox) -> None:
        self._sb = sandbox

    def provision(self) -> None:
        self._sb.build()

    def overlay(self, patch_text: str | None = None, *, isolated: bool = False) -> Overlay:
        return self._sb.overlay(patch_text, isolated=isolated)

    def teardown(self) -> None:
        self._sb.close()

    def pkill_user(self, user: str) -> None:
        self._sb.pkill_user(user)


@register_adapter
class LocalAdapter(_SandboxBackedAdapter):
    name: ClassVar[str] = "local"
    kinds: ClassVar[frozenset[RuntimeKind]] = frozenset({RuntimeKind.REPO})
    grade_cost: ClassVar[GradeCost] = GradeCost.CHEAP

    def __init__(self, task: Task) -> None:
        from rlgate.sandbox.local import LocalSandbox
        super().__init__(LocalSandbox(task.repo_dir))


@register_adapter
class DockerAdapter(_SandboxBackedAdapter):
    name: ClassVar[str] = "docker"
    kinds: ClassVar[frozenset[RuntimeKind]] = frozenset({RuntimeKind.SINGLE_CONTAINER})
    grade_cost: ClassVar[GradeCost] = GradeCost.MODERATE

    def __init__(self, task: Task) -> None:
        from rlgate.sandbox.docker import DockerSandbox
        super().__init__(DockerSandbox.from_task(task))


@register_adapter
class DaytonaAdapter(_SandboxBackedAdapter):
    name: ClassVar[str] = "daytona"
    kinds: ClassVar[frozenset[RuntimeKind]] = frozenset({RuntimeKind.SINGLE_CONTAINER})
    grade_cost: ClassVar[GradeCost] = GradeCost.MODERATE

    def __init__(self, task: Task) -> None:
        from rlgate.sandbox.daytona import DaytonaSandbox
        super().__init__(DaytonaSandbox.from_task(task))
