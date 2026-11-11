"""L0 — the agent's environment has unrestricted internet access (RG307).

Reads whether the task's own config turns outbound network off for the agent's environment
(``EnvSpec.network``, from e.g. ``allow_internet = false``). With open internet the upstream
fix is one download away; controlled egress (mirrors, allow lists, logged requests) is what
the checklist asks for. File-only.
"""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


@register
class OpenInternet(Probe):
    meta = ProbeMeta(
        id="leakage.open_internet",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.LOW,
        doc="The agent's environment allows unrestricted outbound traffic.",
    )

    def applicable(self, task: Task) -> bool:
        return task.workspace_env is not None

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        assert task.workspace_env is not None
        if not task.workspace_env.network:
            return []
        cfg = task.meta.get("config_file")
        return [Finding(
            probe_id=self.meta.id,
            rule_id="RG307",
            title="The agent has unrestricted internet access",
            severity=Severity.LOW,
            verdict=VerdictTier.REVIEW,
            location=Path(cfg).name if cfg else "",
            evidence=Evidence(kind="code_line",
                              detail="the agent's environment doesn't turn outbound network "
                                     "off (no allow_internet = false)",
                              replay=f"cat {cfg}" if cfg else None),
        )]
