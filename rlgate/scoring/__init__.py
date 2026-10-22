"""Scoring-schema plugins: benchmark scoring files -> normalized ScoringModel for the
reward-shape linter (A1–A7). Importing a schema module registers it."""

from __future__ import annotations

from rlgate.scoring.base import ScoringSchema, load_model, register_schema
from rlgate.scoring.model import (
    FEATURE_SIGNALS,
    PARTICIPATION_SIGNALS,
    RewardComponent,
    ScoringModel,
)

__all__ = [
    "FEATURE_SIGNALS",
    "PARTICIPATION_SIGNALS",
    "RewardComponent",
    "ScoringModel",
    "ScoringSchema",
    "load_model",
    "register_schema",
]
