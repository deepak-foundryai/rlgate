"""L3 Agentic audits (``--deep-review``, full run): an LLM agent reads the repository, writes
implementations and runs them against the real verifier, in both directions.

  agentic.strength   is the verifier too weak?   → RG110, proven by a passing wrong
                     implementation, a check that shows it wrong, and the reference passing
                     the same check (agentic/judges.py)
  agentic.ambiguity  is the verifier too strict? → RG408, proven by a rejected alternative
                     that meets the statement just as the reference does

Each finding carries the implementation as a patch and an ``rlgate repro`` command.
"""

from __future__ import annotations

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import CouldNotJudge, NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task


def _meta(pid: str, doc: str) -> ProbeMeta:
    return ProbeMeta(id=pid, family="agentic", kind=ProbeKind.GENERIC,
                     severity=Severity.HIGH, needs_live=True, needs_llm=True,
                     requires=frozenset({Capability.GOLD_PATCH}), doc=doc)


class _Audit(Probe):
    note = ""

    def _get(self, task: Task, ctx: ProbeContext):
        s = ctx.review
        if s is None:
            raise NotApplicable("runs with --deep-review")
        a = s.strength(task, ctx) if self.meta.id == "agentic.strength" else \
            s.ambiguity(task, ctx)
        self.note = a.note
        return a


@register
class StrengthAudit(_Audit):
    meta = _meta("agentic.strength", "An agent writes incomplete or wrong implementations "
                 "and runs them; the verifier must fail each one.")

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        a = self._get(task, ctx)
        if not a.bypasses and not a.finished:
            raise CouldNotJudge(f"the agent stopped at its limit ({a.turns} turns, "
                                f"{a.grades} gradings) without finishing")
        rule = rules.get("RG110")
        out = []
        for b in a.bypasses:
            r = b.requirement
            replay = repro.record(task, "RG110", patch=b.patch, expect="pass",
                                  cmd=task.grader.run,
                                  note=f"apply the agent's implementation, which breaks "
                                       f"{r.id}, and grade")
            lead = f" It confirms first-pass lead {b.lead} (RG109)." if b.lead else ""
            out.append(Finding(
                probe_id=self.meta.id, rule_id="RG110", title=rule.title,
                severity=rule.severity, verdict=VerdictTier.CONFIRMED,
                location="statement" if r.source == "statement" else r.source,
                evidence=Evidence(kind="reproduced_exploit", detail=(
                    f"Requirement {r.id}: {r.text.strip()} (\"{r.quote.strip()}\").\n"
                    f"The agent's implementation {b.how.strip().rstrip('.')}, and the "
                    f"verifier still passes it (reward {b.reward}).\n"
                    f"`{b.check}` fails on that implementation and passes on the reference "
                    f"fix, so the requirement really is broken.{lead}"), replay=replay),
                fix_hint=f"Add a test that fails when {r.id} is broken, for example what "
                         f"`{b.check}` checks."))
        return out


@register
class AmbiguityAudit(_Audit):
    meta = _meta("agentic.ambiguity", "An agent writes a correct alternative the statement "
                 "allows; the verifier must accept it.")

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        a = self._get(task, ctx)
        if not a.alternatives and not a.finished:
            raise CouldNotJudge(f"the agent stopped at its limit ({a.turns} turns, "
                                f"{a.grades} gradings) without finishing")
        rule = rules.get("RG408")
        out = []
        for alt in a.alternatives:
            replay = repro.record(task, "RG408", patch=alt.patch, expect="fail",
                                  cmd=task.grader.run,
                                  note="apply the agent's alternative implementation, which "
                                       "follows the statement, and grade")
            failed = (f" It fails only {', '.join(alt.failed)}; every other graded test "
                      "passes." if alt.failed else "")
            lead = f" It confirms first-pass lead {alt.lead} (RG704)." if alt.lead else ""
            out.append(Finding(
                probe_id=self.meta.id, rule_id="RG408", title=rule.title,
                severity=rule.severity, verdict=VerdictTier.CONFIRMED,
                location=alt.test.split("::", 1)[0] if "::" in alt.test else alt.test,
                evidence=Evidence(kind="reproduced_exploit", detail=(
                    f"The statement says \"{alt.requirement.strip()}\"; {alt.test} asserts "
                    f"\"{alt.assertion.strip()}\".\n"
                    f"An alternative that {alt.why.strip().rstrip('.')} is rejected by the "
                    f"verifier (reward {alt.reward}).{failed}\n"
                    f"`{alt.check}` passes on it and on the reference fix, so by the "
                    f"statement both are correct.{lead}"), replay=replay),
                fix_hint="State this detail in the task, or broaden the test to accept "
                         "every implementation the statement allows."))
        return out
