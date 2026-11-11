"""L4 for rubric-graded tasks (``--deep-review``): attackers write cheap deliverables, a rubric
judge and a goal judge grade each one independently (``review/rubric.py``).

  RG112  the rubric judge passes a deliverable the goal judge says misses the goal: needs
         judgment (both verdicts come from models; the deliverable is attached)
"""

from __future__ import annotations

from rlgate.core import rubric as R
from rlgate.core import rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


@register
class RubricAttack(Probe):
    meta = ProbeMeta(id="agentic.rubric", family="agentic", kind=ProbeKind.GENERIC,
                     severity=Severity.HIGH, needs_llm=True,
                     doc="Attackers write cheap deliverables; a rubric judge and a goal judge "
                         "grade each, and must agree.")
    note = ""

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        if ctx.review is None:
            raise NotApplicable("runs with --deep-review")
        r = R.find(task)
        if r is None:
            raise NotApplicable("the task has no rubric")
        if not (task.instruction or "").strip():
            raise NotApplicable("the task has no statement")
        attacks = ctx.review.rubric_attacks(task, r)
        rule = rules.get("RG112")
        notes, out = [], []
        for a in attacks:
            notes.append(f"{a.attacker}: rubric {a.score:.0%}, goal "
                         f"{'met' if a.achieved else 'missed'}")
            if a.disagree:
                met = ", ".join(i for i, ok in a.met.items() if ok) or "none"
                text = a.deliverable.strip()
                shown = text if len(text) <= 1500 else text[:1500] + "\n…"
                out.append(Finding(
                    probe_id=self.meta.id, rule_id="RG112", title=rule.title,
                    severity=rule.severity, verdict=VerdictTier.REVIEW, location=r.path,
                    evidence=Evidence(kind="diff", detail=(
                        f"A {a.attacker} attacker's deliverable scores {a.score:.0%} on the "
                        f"rubric ({a.rubric_model}; criteria met: {met}), but the goal "
                        f"judge ({a.goal_model}, confidence {a.confidence:.2f}) says it "
                        f"misses the goal: {a.missing.strip()}\n"
                        f"How it games the rubric: {a.how.strip()}\n"
                        f"Deliverable:\n{shown}")),
                    fix_hint=rule.fix))
            elif a.achieved and a.score < 0.5:
                notes[-1] += " (the rubric under-credits a deliverable the goal judge accepts)"
        self.note = "; ".join(notes)
        return out
