"""JUnit XML (the de-facto lingua franca: pytest --junitxml, Jest junit, Surefire, …).

A <testcase> is FAIL if it has a <failure> or <error> child, SKIP if <skipped>, else PASS.
Names are ``classname.name`` when a classname is present, so suites don't collide.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from rlgate.sandbox.parsers.base import (
    ReportParseError,
    TestCase,
    TestReport,
    TestStatus,
    register_parser,
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # strip any {namespace}


@register_parser("junit")
def parse(text: str) -> TestReport:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ReportParseError(f"invalid JUnit XML: {e}") from e

    cases: list[TestCase] = []
    for tc in root.iter():
        if _local(tc.tag) != "testcase":
            continue
        name = tc.get("name", "")
        classname = tc.get("classname", "")
        full = f"{classname}.{name}" if classname else name
        children = {_local(c.tag) for c in tc}
        if "failure" in children or "error" in children:
            status = TestStatus.FAIL
        elif "skipped" in children:
            status = TestStatus.SKIP
        else:
            status = TestStatus.PASS
        cases.append(TestCase(name=full, status=status))
    return TestReport(cases=cases)
