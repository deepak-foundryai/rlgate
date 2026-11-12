"""The few names the vendored SWE-bench log parsers import from the rest of ``swebench``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TestStatus(Enum):
    __test__ = False
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    XFAIL = "XFAIL"


@dataclass
class TestSpec:
    """Only what a parser reads (the instance id and repo version)."""

    __test__ = False
    instance_id: str = ""
    repo: str = ""
    version: str = ""
    log_parser: str = ""
    eval_type: str = "pass_and_fail"
    FAIL_TO_PASS: list[str] = field(default_factory=list)
    PASS_TO_PASS: list[str] = field(default_factory=list)


_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def ansi_escape(text: str) -> str:
    return _ANSI.sub("", text)
