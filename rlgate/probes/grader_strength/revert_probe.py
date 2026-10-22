"""§3.4 — the flagship. Remove one hunk of the gold fix; if the grader still scores it pass,
that hunk is unguarded. Building revert survivors are exploits by construction."""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants.engine import VariantClass


@register
class RevertProbe(Probe):
    meta = ProbeMeta(
        id="grader_strength.revert_probe",
        family="grader_strength",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,   # runs variants in the sandbox
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="A gold fix hunk can be removed and the grader still passes (unguarded line).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        results = ctx.variants.run_revert()
        findings: list[Finding] = []
        for r in results:
            if r.is_exploit and r.klass is VariantClass.DISCRIMINATED:
                findings.append(Finding(
                    probe_id=self.meta.id,
                    title=f"unguarded fix hunk: {r.variant.origin}",
                    severity=Severity.HIGH,
                    verdict=VerdictTier.CONFIRMED,
                    location=r.variant.origin,
                    evidence=Evidence(
                        kind="reproduced_exploit",
                        detail=f"removing this hunk still scores pass (reward={r.reward}); "
                               f"variant {r.variant.id}",
                        replay=f"rlgate replay {task.id} --variant {r.variant.id}",
                    ),
                    fix_hint="add a test that fails when this hunk is absent.",
                ))
        return findings
