"""L1 verifier property "Protected" — test inputs and expected outputs can't be changed by
the agent. With no fix applied, every graded test file the agent can see (in its workspace)
is rewritten so each test function's body is ``pass``. If the grade passes, the grader runs
the agent's copy of the tests: emptying them is a full-reward hack. Proven.

Python test files only. When every graded test is held out, there is nothing to rewrite and
the check is clean.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe
from rlgate.core.probe import NotApplicable, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants import python as py
from rlgate.variants import visible
from rlgate.variants.source import unified_diff


@register
class TestRewrite(PayloadProbe):
    __test__ = False
    meta = ProbeMeta(
        id="forgery.test_rewrite",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.CRITICAL,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="The graded tests in the agent's workspace can be emptied and the grader passes.",
    )
    rule_id = "RG214"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        sp = visible.split(task, ctx.baseline)
        if sp is None:
            raise NotApplicable("can't tell which graded tests are in the workspace without a "
                                "per-test report")
        files: dict[str, str | None] = {}
        for rel in sp.files:
            if not rel.endswith(".py"):
                continue
            try:
                text = (task.repo_dir / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            blank = py.blank_tests(text)
            if blank is not None and blank != text:
                files[rel] = blank
        if not files:
            return []                      # no graded test in the agent's reach
        patch = unified_diff(task.repo_dir, files)
        if not patch:
            return []
        names = sorted(files)
        return [Payload(
            name="empty-tests", patch=patch, location=names[0],
            what=f"Replaced the body of every test in {_some(names)} with `pass`.")]


def _some(names: list[str], n: int = 3) -> str:
    return ", ".join(names[:n]) + (f" (+{len(names) - n} more)" if len(names) > n else "")
