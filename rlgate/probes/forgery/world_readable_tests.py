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

    def _held_out(self, task: Task) -> list[str]:
        """Where the held-out material lives. A grader that brings it in only at grading
        (``inject``: Harbor's /tests, SWE-bench's eval script) names it; otherwise the
        workspace's tests folder (``meta["tests_dir"]``) is what the grader reads."""
        dests = [dest for _, dest in task.grader.inject]
        if dests:
            return dests
        g = task.grader
        if g.env is not None and g.env != task.workspace_env:
            # graded in its own image (Harbor's tests/Dockerfile, a grader service): the
            # held-out tests live there, and the agent's tests/ is the repo's visible suite
            return []
        return [task.meta.get("tests_dir", "tests")]

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        # the agent's view: an overlay before grading. Held-out folders that arrive only at
        # grading aren't there, so this fires only when the image (or the workspace) already
        # holds them readable. A repo's own tests/ folder isn't held out and isn't checked.
        out = []
        with ctx.sandbox.overlay(None) as ovl:
            for tdir in self._held_out(task):
                perms = ovl.read_perms(tdir)
                if perms.world_readable:
                    out.append(self._finding(task, tdir, perms.mode))
        return out

    def _finding(self, task: Task, tdir: str, mode: int) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            title=f"{tdir}/ is world-readable; held-out names leak at runtime",
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED,
            location=tdir,
            evidence=Evidence(
                kind="perms",
                detail=f"{tdir} is readable before grading (mode={mode:#o})",
                replay=f"rlgate check {task.origin or task.id} --only faked",
            ),
            fix_hint="keep held-out tests out of the agent's image (add them only at "
                     "grading), or chmod 700 the graded dir.",
        )
