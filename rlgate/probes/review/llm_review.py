"""L2 Static review: LLM judges that read the task files and execute nothing (``--deep-review``).

First pass, the reference fix visible, each artifact against its own criteria:
statement (RG701-RG703), tests (RG405-RG407), reference fix (RG105, RG106), verifier
command (RG107). Second pass, the reference hidden, three questions in independent votes:
determinability (RG704), enforcement (RG108), weak pass (RG109).

A negative becomes a finding only after ``review/cite.py`` checked its quote and test
against the real files; any valid negative rejects the task. Findings need your judgment
until L3 proves them by execution (weak-pass negatives carry the cheap implementation L3
runs). Statement problems get a rewritten statement that was itself re-reviewed.
"""

from __future__ import annotations

from rlgate.core import claims, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import CouldNotJudge, NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.review import judges as J
from rlgate.review.cite import Negative
from rlgate.review.session import Session, Verdicts

MAX_FINDINGS = 5              # per check; the rest are counted in the note

_WHERE = {"statement": "instruction.md", "reference": "reference fix",
          "verifier": "verifier command", "repo": "repository"}


class _Review(Probe):
    check = ""
    first = True
    note = ""

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        s = ctx.review
        if s is None:
            raise NotApplicable("runs with --deep-review")
        v = s.first(task, self.check) if self.first else s.second(task, self.check)
        self.note = v.note
        fs, leads = self._findings(v, s)
        if fs and self.check in ("weak_pass", "determinability") and \
                getattr(ctx, "sandbox", None) is not None:
            fs = self._settle(task, ctx, s, fs, leads)
        if fs and self.check in ("statement", "determinability"):
            self._rewrite(task, s, fs)
        return fs

    def _rule(self, n: Negative) -> str:
        if self.first:
            return next(c.rule for c in J.FIRST[self.check] if c.name == n.check)
        return J.SECOND[self.check].rule

    def _findings(self, v: Verdicts, s: Session) -> tuple[list[Finding], list[str]]:
        """The findings, and the L3 lead id of each (L1, L2, … as ``Session.leads``)."""
        ranked = s.groups(v)
        if len(ranked) > MAX_FINDINGS:
            self.note += f"; {len(ranked) - MAX_FINDINGS} more problem(s) not shown"
        return ([self._finding(g, v, s) for g in ranked[:MAX_FINDINGS]],
                [f"L{i + 1}" for i in range(min(len(ranked), MAX_FINDINGS))])

    def _settle(self, task: Task, ctx: ProbeContext, s: Session, fs: list[Finding],
                leads: list[str]) -> list[Finding]:
        """Full run: L3 confirms these before anything is discarded. A weak-pass shortcut
        the agent ran and the verifier passed becomes a proven RG110 (reported by the L3
        check); one the verifier rejected is dropped. A determinability gap the repository
        answers is dropped; one proven by a rejected alternative becomes RG408."""
        try:
            audit = s.strength(task, ctx) if self.check == "weak_pass" else \
                s.ambiguity(task, ctx)
        except (CouldNotJudge, NotApplicable) as e:
            self.note += f"; L3 couldn't check these: {e}"
            return fs
        kept: list[Finding] = []
        done: dict[str, list[str]] = {"confirmed": [], "rejected": [], "resolved": []}
        for f, lid in zip(fs, leads, strict=True):
            state = audit.leads.get(lid, "kept")
            claim = f.evidence.detail.splitlines()[0]
            claims.log(task.id, self.check, claim,
                       {"confirmed": claims.CONFIRMED, "rejected": claims.REFUTED,
                        "resolved": claims.REFUTED}.get(state, claims.UNSETTLED),
                       how={"confirmed": "L3 proved it by running it",
                            "rejected": "the verifier rejected the shortcut",
                            "resolved": "the repository answers it"}.get(state, ""))
            if state in done:
                done[state].append(lid)
                continue
            f.evidence.detail += "\nL3 did not settle it, so it still needs your judgment."
            kept.append(f)
        proven = "RG110" if self.check == "weak_pass" else "RG408"
        if done["confirmed"]:
            self.note += (f"; L3 proved {len(done['confirmed'])} by running it (see "
                          f"{proven})")
        if done["rejected"]:
            self.note += (f"; L3 ran {len(done['rejected'])} shortcut(s) and the verifier "
                          "rejected them")
        if done["resolved"]:
            self.note += ("; the repository answers " + ", ".join(
                audit.resolved[x] for x in done["resolved"]))
        return kept

    def _finding(self, group: list[Negative], v: Verdicts, s: Session) -> Finding:
        n = max(group, key=lambda x: x.confidence)
        rid = self._rule(n)
        rule = rules.get(rid)
        models = ", ".join(dict.fromkeys(x.model for x in group))
        who = (f"{len(group)} of {len(v.models)} judges ({models})" if not self.first
               else f"judge {models}")
        where = f"the {n.source}" + (f", {n.test}" if n.test else "")
        lines = [n.gap.strip().rstrip(".") + ".",
                 f"Cited from {where}: \"{n.quote.strip()}\"",
                 (f"Found by {who}, confidence {n.confidence:.2f}. An LLM reading the files "
                 "found this; nothing was run.")]
        fix = n.fix.strip() or rule.fix
        if self.check == "weak_pass":
            lines.insert(1, f"A cheap implementation that would pass: {n.fix.strip()}")
            lines.append("L3 confirms this by running that implementation.")
            fix = rule.fix
        if rule.severity >= Severity.CRITICAL:
            lines.append("An integrity defect: leave the task out of training until it is "
                         "fixed.")
        return Finding(
            probe_id=self.meta.id, rule_id=rid, title=rule.title, severity=rule.severity,
            verdict=VerdictTier.REVIEW, location=_location(n),
            evidence=Evidence(kind="llm_review", detail="\n".join(lines)),
            fix_hint=fix)

    def _rewrite(self, task: Task, s: Session, fs: list[Finding]) -> None:
        """Fixable statement problems: offer a rewritten statement that passed re-review."""
        try:
            rw = s.rewrite(task)
        except CouldNotJudge as e:
            self.note += f"; the statement rewrite didn't finish: {e}"
            return
        if rw is None:
            return
        if rw.clean:
            extra = (f" A rewritten statement that passed re-review is in {rw.path}; "
                     "compare it with instruction.md and adopt what fits.")
        else:
            extra = (f" A rewrite ({rw.path}) still had a problem on re-review "
                     f"({rw.remaining[0].rstrip('.')}), so revise the statement by hand.")
        for f in fs:
            f.fix_hint = (f.fix_hint or "").rstrip() + extra


def _location(n: Negative) -> str:
    if n.source == "tests" and n.test:
        head = n.test.split("::", 1)[0]
        return head if head.endswith((".py", ".js", ".ts", ".go", ".rs", ".java", ".rb")) \
            else n.test
    return _WHERE.get(n.source, n.source)


def _meta(pid: str, severity: Severity, doc: str) -> ProbeMeta:
    return ProbeMeta(id=pid, family="review", kind=ProbeKind.GENERIC, severity=severity,
                     needs_llm=True, doc=doc)


@register
class StatementReview(_Review):
    check = "statement"
    meta = _meta("review.statement", Severity.HIGH,
                 "LLM review: the statement is actionable, leaks nothing, needs nothing "
                 "unseen.")


@register
class TestsReview(_Review):
    check = "tests"
    meta = _meta("review.tests", Severity.HIGH,
                 "LLM review: the tests match the statement, assert real behaviour, accept "
                 "equivalent solutions.")


@register
class ReferenceReview(_Review):
    check = "reference"
    meta = _meta("review.reference", Severity.CRITICAL,
                 "LLM review: the reference fix is on-task, hardcodes nothing, leaves the "
                 "tests alone.")


@register
class VerifierReview(_Review):
    check = "verifier"
    meta = _meta("review.verifier", Severity.CRITICAL,
                 "LLM review: the verifier command can't pass without the tests passing.")


@register
class Determinability(_Review):
    check = "determinability"
    first = False
    meta = _meta("review.determinability", Severity.HIGH,
                 "LLM review, reference hidden: every tested requirement can be known from "
                 "the statement and repo.")


@register
class Enforcement(_Review):
    check = "enforcement"
    first = False
    meta = _meta("review.enforcement", Severity.HIGH,
                 "LLM review, reference hidden: the tests enforce what the statement asks.")


@register
class WeakPass(_Review):
    check = "weak_pass"
    first = False
    meta = _meta("review.weak_pass", Severity.HIGH,
                 "LLM review, reference hidden: no trivial or partial solution passes.")
