"""Read a grader's report out of an overlay and parse it, backend-agnostically.

We ``cat`` the report through ``Overlay.exec`` rather than touching ``overlay.root`` so the
same path works for local, Docker, and Daytona (where the file lives inside the sandbox, not
on the host). Collection must never crash an audit: anything missing or malformed → ``None``.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from rlgate.sandbox.parsers.base import ReportParseError, TestReport, parse_report

if TYPE_CHECKING:
    from rlgate.core.task import ReportSpec
    from rlgate.sandbox.base import Overlay


def collect(overlay: Overlay, spec: ReportSpec) -> TestReport | None:
    res = overlay.exec(f"cat {shlex.quote(spec.path)}")
    if res.exit_code != 0 or not res.stdout.strip():
        return None
    try:
        return parse_report(spec.format, res.stdout)
    except ReportParseError:
        return None
