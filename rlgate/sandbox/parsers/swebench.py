"""SWE-bench test logs, read the way the SWE-bench harness reads them (``get_logs_eval``).

The format is ``swebench:<json>`` with the row's ``log_parser`` name and the fields some
parsers consult (``repo``, ``version``, ``instance_id``). The log is the eval script's whole
output; only the part between the start/end markers is parsed (the whole log if that part
yields nothing). A run SWE-bench would call invalid — the test patch didn't apply, the reset
failed, the markers are missing, nothing ran, or the test command failed while the log shows
no failure (a submission printing fake PASSED lines) — is a ``ReportParseError``, so it
grades as no report at all.
"""

from __future__ import annotations

import json
import re

from rlgate.sandbox.parsers.base import (
    ReportParseError,
    TestCase,
    TestReport,
    TestStatus,
    register_family,
)

START = ">>>>> Start Test Output"
END = ">>>>> End Test Output"
EXIT_CODE = ">>>>> Test Exit Code"
_BAD = (">>>>> Patch Apply Failed", ">>>>> Reset Failed", ">>>>> Tests Errored",
        ">>>>> Tests Timed Out")
_EXIT_RE = re.compile(r">>>>>\ Test\ Exit\ Code:\s*(-?\d+)")
_SUITE_RAN = re.compile(
    r"Executed [1-9]\d* of \d+|TOTAL: [1-9]\d* (?:SUCCESS|FAILED)|[1-9]\d* passing|"
    r"Tests:\s+[1-9]\d*|Test Suites:\s+(?:\d+ \w+, )*[1-9]\d* total|^# tests [1-9]\d*|"
    r"[1-9]\d* specs?, \d+ failures", re.MULTILINE)
_STATUS = {"PASSED": TestStatus.PASS, "XFAIL": TestStatus.PASS, "FAILED": TestStatus.FAIL,
           "ERROR": TestStatus.FAIL, "SKIPPED": TestStatus.SKIP}


def spec_format(log_parser: str, *, repo: str = "", version: str = "",
                instance_id: str = "") -> str:
    return "swebench:" + json.dumps({"parser": log_parser, "repo": repo, "version": version,
                                     "instance_id": instance_id}, separators=(",", ":"))


@register_family("swebench")
def parse(arg: str, text: str) -> TestReport:
    from rlgate.sandbox.parsers.swebench_logs import PARSER_REGISTRY
    from rlgate.sandbox.parsers.swebench_logs._compat import TestSpec
    try:
        cfg = json.loads(arg)
        parser = PARSER_REGISTRY[cfg["parser"]]
    except (ValueError, KeyError, TypeError) as e:
        raise ReportParseError(f"not a known SWE-bench log parser: {arg!r}") from e
    spec = TestSpec(instance_id=cfg.get("instance_id", ""), repo=cfg.get("repo", ""),
                    version=str(cfg.get("version", "")), log_parser=cfg["parser"])
    bad = [b for b in _BAD if b in text]
    if bad:
        raise ReportParseError(f"invalid run: {bad[0].strip('> ')}")
    if START not in text or END not in text:
        raise ReportParseError("the test output markers are missing (the eval script "
                               "did not reach its tests)")
    status = parser(text.split(START)[1].split(END)[0], spec) or parser(text, spec)
    if not status and not _SUITE_RAN.search(text):
        raise ReportParseError("no test results and no sign the suite ran")
    m = _EXIT_RE.search(text)
    code = int(m.group(1)) if m else None
    if code not in (None, 0) and status and not any(
            s in ("FAILED", "ERROR") for s in status.values()):
        raise ReportParseError(f"the test command exited {code} but the log shows no "
                               "failure")
    return TestReport(cases=[TestCase(name, _STATUS.get(s, TestStatus.FAIL), s)
                             for name, s in status.items()])


def record_exit_code(script: str) -> str:
    """SWE-bench's ``record_test_exit_code``: echo the test command's own exit status after
    the end marker (the script's exit status is the final ``git checkout``'s)."""
    lines = script.strip().split("\n")
    if any(EXIT_CODE in ln for ln in lines):
        return script
    for i, ln in enumerate(lines):
        if END in ln:
            lines[i:i + 1] = ["SWEBENCH_TEST_EXIT_CODE=$?", ln,
                              f'echo "{EXIT_CODE}: $SWEBENCH_TEST_EXIT_CODE"']
            break
    return "\n".join(lines) + "\n"
