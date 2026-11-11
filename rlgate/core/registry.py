"""Discovery: a contributor drops a ``@register``-decorated probe into
``rlgate/probes/<family>/`` and it is picked up automatically. External packages ship probes
via the ``rlgate.probes`` entry-point group without touching this repo."""

from __future__ import annotations

import importlib
import pkgutil
from importlib import metadata

from rlgate.core.probe import Probe

_PROBES: dict[str, type[Probe]] = {}


def register(cls: type[Probe]) -> type[Probe]:
    pid = cls.meta.id
    if pid in _PROBES and _PROBES[pid] is not cls:
        raise ValueError(f"duplicate probe id: {pid}")
    _PROBES[pid] = cls
    return cls


def _import_tree(package_name: str) -> None:
    pkg = importlib.import_module(package_name)
    for mod in pkgutil.walk_packages(pkg.__path__, prefix=package_name + "."):
        if not mod.name.rsplit(".", 1)[-1].startswith("_"):
            importlib.import_module(mod.name)


def discover() -> dict[str, type[Probe]]:
    """Import every in-repo probe module, then load third-party entry points. Idempotent."""
    _import_tree("rlgate.probes")
    try:
        eps = metadata.entry_points(group="rlgate.probes")
    except Exception:  # noqa: BLE001 - entry-point loading can raise many things; best-effort
        eps = []
    for ep in eps:
        obj = ep.load()
        if isinstance(obj, type) and issubclass(obj, Probe):
            register(obj)
    return dict(_PROBES)


def all_probes() -> dict[str, type[Probe]]:
    return dict(_PROBES)
