"""Scoring-schema plugin point. A schema turns a task's declared scoring files into a
``ScoringModel``. In-repo schemas live in ``rlgate/scoring/schemas/`` and self-register;
third-party schemas ship via the ``rlgate.scoring_schemas`` entry-point group.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from rlgate.scoring.model import ScoringModel

if TYPE_CHECKING:
    from rlgate.core.task import Task

_SCHEMAS: dict[str, type[ScoringSchema]] = {}


class ScoringSchema(ABC):
    name: str

    @abstractmethod
    def parse(self, path: Path) -> ScoringModel:
        """Read the scoring file(s) rooted at ``path`` and return a normalized model."""


def register_schema(cls: type[ScoringSchema]) -> type[ScoringSchema]:
    if cls.name in _SCHEMAS and _SCHEMAS[cls.name] is not cls:
        raise ValueError(f"duplicate scoring schema: {cls.name}")
    _SCHEMAS[cls.name] = cls
    return cls


def _discover() -> dict[str, type[ScoringSchema]]:
    pkg = importlib.import_module("rlgate.scoring.schemas")
    for mod in pkgutil.walk_packages(pkg.__path__, prefix="rlgate.scoring.schemas."):
        if not mod.name.rsplit(".", 1)[-1].startswith("_"):
            importlib.import_module(mod.name)
    try:
        eps = metadata.entry_points(group="rlgate.scoring_schemas")
    except Exception:  # noqa: BLE001 - best-effort entry-point loading
        eps = []
    for ep in eps:
        obj = ep.load()
        if isinstance(obj, type) and issubclass(obj, ScoringSchema):
            register_schema(obj)
    return dict(_SCHEMAS)


def load_model(task: Task) -> ScoringModel | None:
    """Parse a task's scoring_spec into a ScoringModel, or None if it has none / no schema."""
    if task.scoring_spec is None:
        return None
    schemas = _discover()
    cls = schemas.get(task.scoring_spec.schema)
    if cls is None:
        return None
    return cls().parse(Path(task.scoring_spec.path))
