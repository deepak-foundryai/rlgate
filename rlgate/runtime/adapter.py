"""Execution adapters: the pluggable "how to run it" layer.

An adapter stands a task's environment up, hands out graded overlays (a workspace copy with a
candidate change applied), and tears it down. The single-container/local backends are adapters
wrapping the existing ``Sandbox`` classes; richer kinds — a compose stack, a staged (image-workspace) task
— register the same way (``@register_adapter`` in-repo, or the ``rlgate.runtimes`` entry-point
group for third parties), exactly like probes and ingest adapters. The engine never learns a
benchmark name: it asks ``resolve_adapter`` for whatever the precheck says this task needs.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from enum import Enum
from importlib import metadata
from typing import ClassVar

from rlgate.core.task import Task
from rlgate.runtime.precheck import HostCapabilities, precheck, probe_host
from rlgate.runtime.spec import RuntimeKind
from rlgate.sandbox.base import Overlay


class GradeCost(Enum):
    """How expensive one grade is — the runner uses this for warnings/caps, not correctness."""

    CHEAP = "cheap"          # ~instant: a perms read, a file check in a prebuilt image
    MODERATE = "moderate"    # seconds–minutes: one grader command in one container
    EXPENSIVE = "expensive"  # minutes+: a multi-service stack / real releases


class AdapterUnavailable(RuntimeError):
    """The adapter a task needs can't run here; the message carries the precheck remediation."""


class ExecutionAdapter(ABC):
    name: ClassVar[str]                        # registry key; matches the precheck adapter name
    kinds: ClassVar[frozenset[RuntimeKind]]    # runtime kinds this adapter serves
    grade_cost: ClassVar[GradeCost] = GradeCost.MODERATE

    @abstractmethod
    def provision(self) -> None:
        """Build the image / bring the stack up / prepare the pristine workspace once."""

    @abstractmethod
    def overlay(self, patch_text: str | None = None, *, isolated: bool = False) -> Overlay:
        """A fresh graded overlay on the snapshot, optionally with a patch applied."""

    def pkill_user(self, user: str) -> None:  # default: nothing to kill
        pass

    def teardown(self) -> None:  # default: overlays clean themselves up
        pass

    # --- engine/pipeline compatibility: an adapter is drop-in where a Sandbox was expected ---
    def build(self) -> None:
        self.provision()

    def close(self) -> None:
        self.teardown()


_ADAPTERS: dict[str, type[ExecutionAdapter]] = {}


def register_adapter(cls: type[ExecutionAdapter]) -> type[ExecutionAdapter]:
    if cls.name in _ADAPTERS and _ADAPTERS[cls.name] is not cls:
        raise ValueError(f"duplicate adapter name: {cls.name}")
    _ADAPTERS[cls.name] = cls
    return cls


def adapters() -> dict[str, type[ExecutionAdapter]]:
    """In-repo adapters plus third-party ``rlgate.runtimes`` entry points. Idempotent."""
    importlib.import_module("rlgate.runtime.adapters")
    pkg = importlib.import_module("rlgate.runtime.adapters")
    for mod in pkgutil.walk_packages(pkg.__path__, prefix="rlgate.runtime.adapters."):
        if not mod.name.rsplit(".", 1)[-1].startswith("_"):
            importlib.import_module(mod.name)
    try:
        eps = metadata.entry_points(group="rlgate.runtimes")
    except Exception:  # noqa: BLE001 - best-effort entry-point loading
        eps = []
    for ep in eps:
        obj = ep.load()
        if isinstance(obj, type) and issubclass(obj, ExecutionAdapter):
            register_adapter(obj)
    return dict(_ADAPTERS)


def resolve_adapter(task: Task, prefer: str = "auto",
                    host: HostCapabilities | None = None) -> ExecutionAdapter:
    """Pick and instantiate the adapter for a task. ``prefer`` in {local,docker,daytona} forces
    a backend (single-container/repo); ``auto`` uses the precheck's runtime decision."""
    host = host or probe_host()
    registry = adapters()

    if prefer in ("local", "docker", "daytona"):
        from rlgate.sandbox.select import BackendUnavailable, resolve_backend
        choice = resolve_backend(task, prefer)
        if not choice.usable:
            raise AdapterUnavailable(f"{task.id}: backend '{choice.name}' unavailable: "
                                     f"{choice.unavailable}")
        try:
            return registry[choice.name](task)  # type: ignore[call-arg]
        except KeyError as e:  # pragma: no cover - built-ins always registered
            raise AdapterUnavailable(f"no adapter '{choice.name}'") from e
        except BackendUnavailable as e:
            raise AdapterUnavailable(str(e)) from e

    plan = precheck(task, host)
    if not plan.usable:
        raise AdapterUnavailable(f"{task.id}: {plan.reason} — {plan.remediation}")
    key = plan.adapter.split(":", 1)[0]
    cls = registry.get(key)
    if cls is None:
        raise AdapterUnavailable(
            f"{task.id}: runtime '{plan.spec.kind.value}' needs the '{key}' adapter, which "
            f"isn't installed yet ({plan.reason}). Run `rlgate precheck` for details.")
    return cls(task)  # type: ignore[call-arg]
