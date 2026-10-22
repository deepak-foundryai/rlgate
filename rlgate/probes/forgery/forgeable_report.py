"""§3.1 — the grader's machine-readable report is written to a relative path, i.e. into the
grader's cwd, which is the candidate's own workspace. If any scoring path trusts a report that
is already on disk (rather than regenerating it in an isolated, read-only location), the
candidate can forge a passing report. We can't prove at read-time forgeability statically, so
this is REVIEW: a forgeable verdict channel that the live fail-wins check should confirm.
"""

from __future__ import annotations

import os

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task


@register
class ForgeableReport(Probe):
    meta = ProbeMeta(
        id="forgery.forgeable_report",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        requires=frozenset({Capability.PER_TEST}),
        doc="Grader report path is in the candidate's writable tree (forgeable verdict channel).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        path = task.grader.report.path
        if os.path.isabs(path):
            return []  # an out-of-tree absolute path is presumed isolated from the candidate
        return [Finding(
            probe_id=self.meta.id,
            title=f"verdict channel in the candidate's writable tree: {path}",
            severity=Severity.HIGH,
            verdict=VerdictTier.REVIEW,
            location=path,
            evidence=Evidence(
                kind="code_line",
                detail=f"grader reads its report from the relative path {path!r}; it is written "
                       "into the grader cwd (the candidate workspace), so a pre-written report "
                       "is forgeable unless scoring regenerates it in an isolated location.",
                replay=f"rlgate audit {task.id} --probe {self.meta.id}",
            ),
            fix_hint="write + read the report under a read-only, out-of-tree path the candidate "
                     "cannot touch; regenerate it in the verifier container.",
        )]
