"""Per-test report model + a tiny format registry.

A parser turns a grader's machine-readable report (JUnit XML, ``go test -json``, a .trx
file, Jest/Vitest JSON) into a format-agnostic ``TestReport``. Everything downstream
(baseline per-test map, variant discrimination) speaks ``TestReport``, never a raw format.
Adding a format is one ``@register_parser`` function — no other file changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class ReportParseError(ValueError):
    """Raised by a parser when the report is malformed for its declared format."""


class TestStatus(Enum):
    __test__ = False  # not a pytest test class despite the name
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class TestCase:
    __test__ = False
    name: str
    status: TestStatus


@dataclass
class TestReport:
    __test__ = False
    cases: list[TestCase] = field(default_factory=list)

    @property
    def status_by_name(self) -> dict[str, TestStatus]:
        """Last status wins when a name repeats (retries, parametrized reruns)."""
        return {c.name: c.status for c in self.cases}

    @property
    def names(self) -> list[str]:
        return list(self.status_by_name)

    @property
    def passed(self) -> list[str]:
        return [n for n, s in self.status_by_name.items() if s is TestStatus.PASS]

    @property
    def failed(self) -> list[str]:
        return [n for n, s in self.status_by_name.items() if s is TestStatus.FAIL]

    @property
    def total(self) -> int:
        return len(self.status_by_name)

    @property
    def pass_fraction(self) -> float:
        graded = [s for s in self.status_by_name.values() if s is not TestStatus.SKIP]
        return (sum(s is TestStatus.PASS for s in graded) / len(graded)) if graded else 0.0

    def outcome_reward(self, pass_reward: float = 1.0) -> float:
        """Full reward only if at least one test was graded and none failed."""
        statuses = set(self.status_by_name.values())
        graded = statuses - {TestStatus.SKIP}
        if not graded or TestStatus.FAIL in statuses:
            return 0.0
        return pass_reward


# --- format registry ----------------------------------------------------------------

_PARSERS: dict[str, Callable[[str], TestReport]] = {}


def register_parser(name: str) -> Callable[[Callable[[str], TestReport]], Callable[[str], TestReport]]:
    def deco(fn: Callable[[str], TestReport]) -> Callable[[str], TestReport]:
        _PARSERS[name] = fn
        return fn
    return deco


def known_formats() -> list[str]:
    return sorted(_PARSERS)


def parse_report(fmt: str, text: str) -> TestReport:
    try:
        parser = _PARSERS[fmt]
    except KeyError:
        raise ReportParseError(
            f"unknown report format {fmt!r}; known: {known_formats()}"
        ) from None
    return parser(text)
