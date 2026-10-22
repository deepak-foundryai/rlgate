"""§3.1 — held-out sources / scorer readable by the candidate UID (not chmod 700)."""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


@register
class WorldReadableTests(Probe):
    meta = ProbeMeta(
        id="forgery.world_readable_tests",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,   # inspects runtime perms via a live overlay
        doc="/tests (held-out sources, scorer) is readable by the candidate UID.",
    )

    # The directory holding graded sources; overridable via task.meta without special-casing.
    def _tests_dir(self, task: Task) -> str:
        return task.meta.get("tests_dir", "tests")

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        tdir = self._tests_dir(task)
        with ctx.sandbox.overlay(None) as ovl:
            perms = ovl.read_perms(tdir)
        if perms.world_readable:
            return [Finding(
                probe_id=self.meta.id,
                title=f"{tdir}/ is world-readable; held-out names leak at runtime",
                severity=Severity.HIGH,
                verdict=VerdictTier.CONFIRMED,
                location=tdir,
                evidence=Evidence(
                    kind="perms",
                    detail=f"mode={perms.mode:#o}",
                    replay=f"rlgate audit <task> --probe {self.meta.id}",
                ),
                fix_hint="chmod 700 the graded dir; delete graded sources after build.",
            )]
        return []
