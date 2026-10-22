"""A task *bank* is many tasks at once. Three ways to produce them, none touching the core:

  1. discover: a directory tree of ``task.rlgate.yaml`` descriptors (zero config);
  2. a declarative mapping — a bundled ``--preset`` (mimo/swebench/harbor/deepswe) or a
     user-supplied ``bank.rlgate.yaml`` for any other layout (config, not code);
  3. an entry-point Python adapter (``rlgate.adapters`` group) — the escape hatch for layouts
     a mapping can't express.

All three yield the same ``list[Task]`` the pipeline audits one by one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from importlib import metadata
from pathlib import Path

from rlgate.core.task import Task
from rlgate.ingest import spec
from rlgate.ingest.mapping import BankMapping


def discover_tasks(root: str | Path) -> Iterator[Task]:
    """Every ``task.rlgate.yaml`` under ``root`` (recursive), sorted by path."""
    for descriptor in sorted(Path(root).rglob(spec.DESCRIPTOR_NAME)):
        yield spec.load(descriptor)


def detect_layout(target: str | Path) -> str | None:
    """Guess a bundled preset from the directory shape, so a bank of task dirs 'just works'
    without --preset. Returns a preset name or None (→ discover task.rlgate.yaml)."""
    p = Path(target)
    if p.is_file():
        return "swebench" if p.suffix in (".jsonl", ".json") else None
    if not p.is_dir():
        return None
    if (p / "envs").is_dir():
        return "mimo"
    subdirs = [d for d in p.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]
    if not subdirs:
        return None
    harbor = sum((d / "instruction.md").exists()
                 and ((d / "task.toml").exists() or (d / "task.json").exists())
                 for d in subdirs)
    mimo = sum((d / "env.json").exists() for d in subdirs)
    half = max(1, len(subdirs) // 2)
    if harbor >= half:
        return "harbor"
    if mimo >= half:
        return "mimo"
    return None


def load_bank(target: str | Path, preset: str | None = None,
              mapping: str | Path | None = None, adapter: str | None = None,
              limit: int | None = None) -> list[Task]:
    if adapter:
        tasks: Iterable[Task] = _entry_point_adapter(adapter)(target)
    elif mapping:
        tasks = BankMapping.from_file(mapping).resolve_all(target)
    elif preset:
        from rlgate.ingest.presets import load_preset
        tasks = load_preset(preset).resolve_all(target)
    else:
        tasks = discover_tasks(target)
    return list(_capped(tasks, limit))


def _entry_point_adapter(name: str):
    for ep in metadata.entry_points(group="rlgate.adapters"):
        if ep.name == name:
            return ep.load()
    raise ValueError(f"no rlgate.adapters entry point named {name!r}")


def _capped(items: Iterable[Task], limit: int | None) -> Iterator[Task]:
    for i, t in enumerate(items):
        if limit is not None and i >= limit:
            return
        yield t
