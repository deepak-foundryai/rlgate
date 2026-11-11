"""Jest / Vitest JSON (``--json`` / ``--reporter=json``): a top-level object with
``testResults[]``, each holding ``assertionResults[]`` whose ``status`` is
passed / failed / pending / skipped / todo. ``fullName`` disambiguates across files.
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
    "pending": TestStatus.SKIP,
    "skipped": TestStatus.SKIP,
    "todo": TestStatus.SKIP,
    "disabled": TestStatus.SKIP,
}


@register_parser("vitest_json")
def parse(text: str) -> TestReport:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReportParseError(f"invalid Vitest/Jest JSON: {e}") from e
    if not isinstance(data, dict):
        raise ReportParseError("expected a JSON object at the top level")

    cases: list[TestCase] = []
    for file_res in data.get("testResults", []):
        for a in file_res.get("assertionResults", []):
            name = a.get("fullName") or a.get("title") or ""
            if not name:
                continue
            status = _STATUS.get((a.get("status") or "").lower(), TestStatus.SKIP)
            cases.append(TestCase(name=name, status=status))
    return TestReport(cases=cases)
