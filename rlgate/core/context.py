"""Shared, expensive state built once and handed to every probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rlgate.sandbox.base import Sandbox
    from rlgate.variants.engine import VariantEngine


@dataclass
class Baseline:
    """Phase B result: oracle/nop at k runs, flaky checks, and the per-test→region map."""

    oracle: float                       # gold reward (want 1.0)
    nop: float                          # empty-submission reward (want 0.0)
    k: int
    flaky_checks: frozenset[str] = frozenset()   # checks that disagreed across k runs
    per_test: dict[str, bool] = field(default_factory=dict)  # test id → passed under gold
    ok: bool = False                    # oracle==1.0 and nop==0.0 and no flaky checks

    @property
    def sane(self) -> bool:
        return self.oracle == 1.0 and self.nop == 0.0 and not self.flaky_checks


class ResultCache:
    """Content-addressed cache so identical variant runs are free across a session."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get_or_run(self, key: str, fn):
        if key not in self._store:
            self._store[key] = fn()
        return self._store[key]


@dataclass
class ProbeContext:
    sandbox: Sandbox
    baseline: Baseline
    variants: VariantEngine
    cache: ResultCache = field(default_factory=ResultCache)
    config: dict = field(default_factory=dict)
