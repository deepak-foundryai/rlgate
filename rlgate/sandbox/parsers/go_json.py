"""``go test -json``: a stream of newline-delimited JSON events.

Each event has an Action ("run"/"pass"/"fail"/"skip"/"output"/...) and, for test-level
events, a Test and Package. The terminal pass/fail/skip action for a (Package, Test) is the
test's result. Events without a Test field are package-level and ignored.
"""

from __future__ import annotations

import json

from rlgate.sandbox.parsers.base import (
    TestCase,
    TestReport,
    TestStatus,
    register_parser,
)

_TERMINAL = {"pass": TestStatus.PASS, "fail": TestStatus.FAIL, "skip": TestStatus.SKIP}


@register_parser("go_json")
def parse(text: str) -> TestReport:
    last: dict[str, TestStatus] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate interleaved non-JSON build noise
        test = ev.get("Test")
        action = ev.get("Action")
        if not test or action not in _TERMINAL:
            continue
        pkg = ev.get("Package", "")
        name = f"{pkg}.{test}" if pkg else test
        last[name] = _TERMINAL[action]
    return TestReport(cases=[TestCase(name=n, status=s) for n, s in last.items()])
