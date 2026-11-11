"""L1 verdict channel — report forgery. With no fix applied, the agent writes a passing test
report where the grader reads one (JUnit XML, CTRF JSON, Vitest JSON, go test -json, TRX),
listing every test the reference passes, and a passing reward file where the grader reads
its reward. Written from the agent's environment, so a separate verifier only sees it if
the harness carries it over. If the grade passes, the grader trusts a report it didn't
produce. Proven — the live counterpart of RG210's file-level warning.
"""

from __future__ import annotations

import json
import posixpath
from xml.sax.saxutils import quoteattr

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe, write_file
from rlgate.core.probe import NotApplicable, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


def fake_report(fmt: str, names: list[str]) -> str | None:
    """A report in ``fmt`` where every test in ``names`` passed (None: unknown format)."""
    names = names or ["test_rlgate_forged"]
    if fmt == "junit":
        cases = "".join(f"<testcase classname={quoteattr(_cls(n))} name={quoteattr(_leaf(n))}"
                        f" time=\"0.001\"/>" for n in names)
        return (f"<?xml version=\"1.0\" encoding=\"utf-8\"?><testsuites><testsuite "
                f"name=\"pytest\" tests=\"{len(names)}\" failures=\"0\" errors=\"0\" "
                f"skipped=\"0\">{cases}</testsuite></testsuites>")
    if fmt == "ctrf":
        return json.dumps({"results": {
            "tool": {"name": "pytest"},
            "summary": {"tests": len(names), "passed": len(names), "failed": 0,
                        "skipped": 0, "pending": 0, "other": 0},
            "tests": [{"name": n, "status": "passed", "duration": 1} for n in names]}})
    if fmt == "vitest_json":
        return json.dumps({"numTotalTests": len(names), "numPassedTests": len(names),
                           "numFailedTests": 0, "success": True, "testResults": [{
                               "status": "passed", "assertionResults": [
                                   {"fullName": n, "title": _leaf(n), "status": "passed"}
                                   for n in names]}]})
    if fmt == "go_json":
        return "\n".join(json.dumps({"Action": "pass", "Package": "rlgate", "Test": n})
                         for n in names)
    if fmt == "trx":
        rows = "".join(f"<UnitTestResult testName={quoteattr(n)} outcome=\"Passed\"/>"
                       for n in names)
        return (f"<?xml version=\"1.0\" encoding=\"utf-8\"?><TestRun><Results>{rows}"
                f"</Results></TestRun>")
    return None


def _leaf(n: str) -> str:
    return n.replace("::", ".").rsplit(".", 1)[-1]


def _cls(n: str) -> str:
    parts = n.replace("::", ".").rsplit(".", 1)
    return parts[0] if len(parts) == 2 else ""


def _where(task: Task, path: str) -> str:
    """Where the agent writes ``path``: relative paths resolve in the grader's cwd."""
    if path.startswith("/"):
        return path
    base = task.grader.cwd
    return posixpath.join(base, path) if base else path


@register
class ReportForgery(PayloadProbe):
    meta = ProbeMeta(
        id="forgery.report_forgery",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        doc="A passing test report or reward file written by the agent scores a pass.",
    )
    rule_id = "RG203"

    def applicable(self, task: Task) -> bool:
        g = task.grader
        return super().applicable(task) and (g.report is not None or bool(g.reward_files))

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        g = task.grader
        names = sorted(t for t, ok in ctx.baseline.per_test.items() if ok)
        cmds: list[str] = []
        planted: list[str] = []
        if g.report is not None:
            body = fake_report(g.report.format, names)
            if body is not None:
                where = _where(task, g.report.path)
                cmds.append(write_file(where, body))
                planted.append(where)
        for rf in g.reward_files:
            where = _where(task, rf)
            body = (json.dumps({g.reward_key: g.pass_reward}) if rf.endswith(".json")
                    else f"{g.pass_reward}")
            cmds.append(write_file(where, body))
            planted.append(where)
        if not cmds:
            raise NotApplicable(f"no forged report for the {g.report.format if g.report else ''}"
                                " format yet")
        # the plain plant, and the same files made read-only (a grader running as another
        # user can't overwrite them)
        ro = [f"chmod 444 {_q(p)} 2>/dev/null || true" for p in planted]
        outside = any(p.startswith("/") for p in planted)   # beyond the workspace
        return [
            Payload(name="fake-report", patch=None, setup=tuple(cmds), isolated=outside,
                    location=planted[0],
                    what=f"Wrote a passing report for {len(names) or 1} test(s) at "
                         f"{', '.join(planted)}."),
            Payload(name="fake-report-readonly", patch=None, setup=tuple(cmds + ro),
                    isolated=outside, location=planted[0],
                    what=f"Wrote a passing, read-only report at {', '.join(planted)}."),
        ]


def _q(p: str) -> str:
    import shlex
    return shlex.quote(p)
