"""L0 — the task declares the resource limits it is checked and trained under (RG012).

Reads what the task's own config declares (``Task.meta["limits"]``: name → value or None, set
by the loader). A limit left out means training runs the task under the harness's defaults,
which may not be what was checked. File-only.
"""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


@register
class DeclaredLimits(Probe):
    meta = ProbeMeta(
        id="hygiene.limits",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.LOW,
        doc="The task declares its CPU, memory and time limits.",
    )

    def applicable(self, task: Task) -> bool:
        return isinstance(task.meta.get("limits"), dict)

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        missing = [k for k, v in task.meta["limits"].items() if v in (None, "", 0)]
        if not missing:
            return []
        cfg = task.meta.get("config_file")
        return [Finding(
            probe_id=self.meta.id,
            rule_id="RG012",
            title="The task doesn't declare its resource limits",
            severity=Severity.LOW,
            verdict=VerdictTier.REVIEW,
            location=Path(cfg).name if cfg else "",
            evidence=Evidence(kind="code_line",
                              detail=f"not declared: {', '.join(missing)}",
                              replay=f"cat {cfg}" if cfg else None),
        )]
