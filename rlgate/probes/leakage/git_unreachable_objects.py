"""§3.3 — the reference fix survives as an unreachable git object (the MiMo/SQLGlot leak).

We read the object store directly as the auditor, so an agent-side "git commands blocked"
guard is irrelevant — if the object is on disk, the answer is on disk."""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task


@register
class GitUnreachableObjects(Probe):
    meta = ProbeMeta(
        id="leakage.git_unreachable_objects",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        requires=frozenset({Capability.GIT_REPO}),
        doc="Unreachable git objects (deleted-but-unpruned branches) may hold the fix.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        with ctx.sandbox.overlay(None) as ovl:
            res = ovl.exec("git fsck --unreachable --no-reflogs 2>/dev/null | head -n 50")
        lines = [ln for ln in res.stdout.splitlines() if "unreachable commit" in ln]
        if not lines:
            return []
        sample = lines[0].split()[-1][:12]
        return [Finding(
            probe_id=self.meta.id,
            title=f"{len(lines)} unreachable git commit(s) on disk (e.g. {sample})",
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED,
            location=".git",
            evidence=Evidence(
                kind="reproduced_exploit",
                detail="later history was deleted but not pruned; readable via the object store",
                replay="git fsck --unreachable --no-reflogs",
            ),
            fix_hint="git reflog expire --expire=now --all && git gc --prune=now; "
                     "or vendor a clean snapshot at the task base commit.",
        )]
