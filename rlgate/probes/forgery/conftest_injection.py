"""§3.1 — the CONFIRMING counterpart to unrestored_test_infra, for pytest tasks. Inject a
root conftest.py whose makereport hook forces every test to "passed", apply it on the
feature-less base (where honest work fails), and run the grader. If the grader now scores
pass, the verdict channel is forgeable — proven by execution, not inferred. CONFIRMED.

This is a PATTERN probe: a pytest-specific payload, run live. If the task doesn't use pytest,
or already ships a root conftest.py, the payload simply doesn't take and we report nothing.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.sandbox._patch import PatchConflict

_HOOK = [
    "import pytest",
    "",
    "",
    "@pytest.hookimpl(hookwrapper=True)",
    "def pytest_runtest_makereport(item, call):",
    "    outcome = yield",
    "    rep = outcome.get_result()",
    "    rep.outcome = 'passed'",
    "    rep.longrepr = None",
]


def _conftest_patch() -> str:
    body = "".join(f"+{line}\n" for line in _HOOK)
    return f"--- /dev/null\n+++ b/conftest.py\n@@ -0,0 +1,{len(_HOOK)} @@\n{body}"


@register
class ConftestInjection(Probe):
    meta = ProbeMeta(
        id="forgery.conftest_injection",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.HIGH,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="A candidate-injected root conftest.py forces pytest to report every test passed.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        # Only attributable when honest-empty genuinely fails: a forced pass is then forgery,
        # not a pre-broken grader.
        if ctx.baseline.nop != 0.0:
            return []
        try:
            overlay = ctx.sandbox.overlay(_conftest_patch())
        except PatchConflict:
            return []  # a root conftest.py already exists → unrestored_test_infra covers it
        with overlay:
            res = overlay.exec(task.grader.run)
        if res.exit_code != 0:
            return []
        return [Finding(
            probe_id=self.meta.id,
            title="injected conftest.py forges a passing verdict",
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED,
            location="conftest.py",
            evidence=Evidence(
                kind="reproduced_exploit",
                detail="adding a root conftest.py with a makereport hook (no feature work) made "
                       "the grader score pass; the grader does not restore test infrastructure.",
                replay=f"rlgate audit {task.id} --probe {self.meta.id}",
            ),
            fix_hint="restore conftest.py (and all test infra) from a read-only reference before "
                     "scoring; run the suite in a verifier container the candidate can't write.",
        )]
