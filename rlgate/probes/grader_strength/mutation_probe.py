"""§3.4 — mutation testing without an LLM. Apply single-token/line mutations to the gold fix
(relational, logical, boolean, increment, stub-return) and run the per-test subset.

A mutant the suite KILLS is good coverage. A mutant that SURVIVES (builds, no gold-passing
test regresses) is ambiguous — it may be an equivalent mutant (benign) or a genuine coverage
gap — so every survivor is reported at REVIEW, never CONFIRMED. That split is the whole point:
revert survivors are non-equivalent by construction (CONFIRMED); mutants need adjudication.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants.engine import VariantClass, VariantEngine


@register
class MutationProbe(Probe):
    meta = ProbeMeta(
        id="grader_strength.mutation_probe",
        family="grader_strength",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        needs_live=True,   # runs variants in the sandbox
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="A single-token mutation of the gold fix survives the suite (possible coverage gap).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        results = ctx.variants.run_mutations()
        survivors = [
            r for r in results
            if r.is_exploit and r.klass is VariantClass.DISCRIMINATED
        ]
        if not survivors:
            return []

        score = VariantEngine.discrimination_score(results)
        # Collapse by origin ("path#hunkN:operator"); many lines can share one site label.
        by_origin: dict[str, int] = {}
        exemplar: dict[str, str] = {}
        for r in survivors:
            by_origin[r.variant.origin] = by_origin.get(r.variant.origin, 0) + 1
            exemplar.setdefault(r.variant.origin, r.variant.id)

        findings: list[Finding] = []
        for origin, count in by_origin.items():
            operator = origin.rsplit(":", 1)[-1]
            sites = f"{count} site{'s' if count > 1 else ''}"
            findings.append(Finding(
                probe_id=self.meta.id,
                title=f"mutant survives the suite ({operator}): {origin}",
                severity=Severity.MEDIUM,
                verdict=VerdictTier.REVIEW,
                location=origin,
                evidence=Evidence(
                    kind="variant",
                    detail=f"a {operator} mutation of the gold fix still scores pass at "
                           f"{sites} (mutation score {score:.2f}); equivalent-mutant vs "
                           f"coverage-gap needs adjudication; variant {exemplar[origin]}",
                    replay=f"rlgate replay {task.id} --variant {exemplar[origin]}",
                ),
                fix_hint="add an assertion that distinguishes this behavior, or confirm the "
                         "mutant is equivalent (no observable change) and whitelist it.",
            ))
        return findings
