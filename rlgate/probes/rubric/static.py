"""Static rubric checks for rubric-graded tasks (no LLM, no run):

  RG409  a criterion bundles several checks, or uses vague words with nothing measurable
  RG410  duplicate criteria; flat or missing weights where some criteria guard against
         serious mistakes; no penalty criteria
  RG411  a requirement in the statement shares no key words with any criterion

Word-level heuristics, so every finding needs judgment.
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
class RubricStatic(Probe):
    meta = ProbeMeta(id="rubric.static", family="rubric", kind=ProbeKind.GENERIC,
                     severity=Severity.MEDIUM,
                     doc="Rubric criteria must be atomic, observable, distinct, weighted by "
                         "consequence, and cover every requirement.")
    note = ""

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        r = R.find(task)
        if r is None:
            raise NotApplicable("the task has no rubric")
        self.note = f"{len(r.criteria)} criteria in {r.path}"
        out: list[Finding] = []
        for c in r.criteria:
            why = [w for w in (R.not_atomic(c), R.not_observable(c)) if w]
            if why:
                out.append(self._f("RG409", r, f"{c.id}: \"{c.text}\" — {'; '.join(why)}."))
        for a, b in R.duplicates(r):
            out.append(self._f("RG410", r, f"{a.id} and {b.id} check the same thing: "
                                           f"\"{a.text}\" / \"{b.text}\"."))
        for p in R.weight_problems(r):
            out.append(self._f("RG410", r, f"Weights: {p}."))
        if (task.instruction or "").strip():
            for req in R.uncovered(task.instruction or "", r)[:6]:
                out.append(self._f("RG411", r, f"The statement asks: \"{req[:240]}\" — no "
                                               "criterion shares its key words."))
        return out

    def _f(self, rule_id: str, r: R.Rubric, detail: str) -> Finding:
        rule = rules.get(rule_id)
        return Finding(probe_id=self.meta.id, rule_id=rule_id, title=rule.title,
                       severity=rule.severity, verdict=VerdictTier.REVIEW, location=r.path,
                       evidence=Evidence(kind="code_line", detail=detail),
                       fix_hint=rule.fix)
