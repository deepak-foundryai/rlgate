"""Report parsers: grader output (JUnit/go-json/trx/vitest) -> format-agnostic TestReport.

Importing this package registers every bundled parser. ``parse_report(fmt, text)`` dispatches
by the ``ReportSpec.format`` name; ``collect(overlay, spec)`` reads + parses in one step.
"""

from __future__ import annotations

from rlgate.sandbox.parsers import go_json, junit, trx, vitest_json  # noqa: F401 - register
from rlgate.sandbox.parsers.base import (
    ReportParseError,
    TestCase,
    TestReport,
    TestStatus,
    known_formats,
    parse_report,
    register_parser,
)
from rlgate.sandbox.parsers.collect import collect

__all__ = [
    "ReportParseError",
    "TestCase",
    "TestReport",
    "TestStatus",
    "collect",
    "known_formats",
    "parse_report",
    "register_parser",
]
