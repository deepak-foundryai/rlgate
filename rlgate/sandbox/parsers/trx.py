"""VSTest .trx (MSTest / dotnet test): XML with <UnitTestResult> rows carrying a
``testName`` and an ``outcome`` attribute (Passed / Failed / NotExecuted / Inconclusive)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from rlgate.sandbox.parsers.base import (
    ReportParseError,
    TestCase,
    TestReport,
    TestStatus,
    register_parser,
)

_OUTCOME = {
    "passed": TestStatus.PASS,
    "failed": TestStatus.FAIL,
    "notexecuted": TestStatus.SKIP,
    "inconclusive": TestStatus.SKIP,
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@register_parser("trx")
def parse(text: str) -> TestReport:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ReportParseError(f"invalid TRX XML: {e}") from e

    cases: list[TestCase] = []
    for el in root.iter():
        if _local(el.tag) != "UnitTestResult":
            continue
        name = el.get("testName")
        if not name:
            continue
        status = _OUTCOME.get((el.get("outcome") or "").lower(), TestStatus.SKIP)
        cases.append(TestCase(name=name, status=status))
    return TestReport(cases=cases)
