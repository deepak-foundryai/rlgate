"""§3.9 / B-series — over-fit tests, proven by execution. Apply the gold fix, then vary an
incidental the spec leaves free (a message/log/raise string not named in the instruction). A
correct solution is unaffected, so the grade must stay pass; if a test breaks, that test is
pinned to an incidental and an alternative correct solution would fail it. CONFIRMED.

This is the deterministic answer to "instruction↔test alignment": we don't read the
instruction's meaning — we change something it leaves free and let execution decide. Semantic
alignment ("does this test verify requirement N") stays DEFERRED to the LLM layer.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants.engine import VariantClass


@register
class OverfitEquivalence(Probe):
    meta = ProbeMeta(
        id="test_quality.overfit_equivalence",
        family="test_quality",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.GOLD_PATCH, Capability.INSTRUCTION}),
        doc="A test breaks when an incidental (spec-free) string in the gold fix is varied.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        # Premise: the gold solution must score pass, or "equivalent" is meaningless.
        if ctx.baseline.oracle != task.grader.pass_reward:
            return []

        findings: list[Finding] = []
        for r in ctx.variants.run_equivalence():
            if r.klass is VariantClass.DID_NOT_BUILD:
                continue  # the transform didn't apply/build → not an over-fit signal
            if r.reward >= task.grader.pass_reward:
                continue  # grade unchanged → the test is fair w.r.t. this incidental
            broke = f"; broke {r.broke}" if r.broke else ""
            findings.append(Finding(
                probe_id=self.meta.id,
                title=f"over-fit test: grade drops when an incidental string varies ({r.variant.origin})",
                severity=Severity.HIGH,
                verdict=VerdictTier.CONFIRMED,
                location=r.variant.origin,
                evidence=Evidence(
                    kind="reproduced_exploit",
                    detail=f"varying a message/log string the instruction never names dropped the "
                           f"reward to {r.reward}{broke}; a correct solution free to choose that "
                           f"string would fail. variant {r.variant.id}",
                    replay=f"rlgate replay {task.id} --variant {r.variant.id}",
                ),
                fix_hint="assert the required behavior, not an incidental message; or add the "
                         "string to the instruction if it is actually a requirement.",
            ))
        return findings
