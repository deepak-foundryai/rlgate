"""CTRF (Common Test Report Format, ``pytest --ctrf``, Harbor's ``/logs/verifier/ctrf.json``):
``{"results": {"tests": [{"name": ..., "status": "passed" | "failed" | ...}]}}``.
"""

from __future__ import annotations

import json

from rlgate.sandbox.parsers.base import (
    ReportParseError,
    TestCase,
    TestReport,
    TestStatus,
    register_parser,
)

_STATUS = {
    "passed": TestStatus.PASS,
    "failed": TestStatus.FAIL,
    "skipped": TestStatus.SKIP,
    "pending": TestStatus.SKIP,
    "other": TestStatus.FAIL,     # errors (e.g. a fixture crashed) count against the test
}


@register_parser("ctrf")
def parse(text: str) -> TestReport:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReportParseError(f"invalid CTRF JSON: {e}") from e
    tests = (data.get("results") or {}).get("tests") if isinstance(data, dict) else None
    if not isinstance(tests, list):
        raise ReportParseError("expected results.tests[] in a CTRF report")
    cases: list[TestCase] = []
    for t in tests:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        name = str(t["name"])
        if t.get("suite") and isinstance(t["suite"], str) and t["suite"] not in name:
            name = f"{t['suite']}::{name}"
        cases.append(TestCase(name, _STATUS.get(str(t.get("status", "")).lower(),
                                                TestStatus.FAIL)))
    return TestReport(cases=cases)
