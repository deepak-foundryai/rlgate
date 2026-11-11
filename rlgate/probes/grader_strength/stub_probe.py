"""L1 coverage — stub probe. Every Python function the reference fix changes is replaced, one
at a time, by a stub (``return None``; ``raise NotImplementedError``), on top of the rest of
the fix. A stub that still scores pass means nothing checks that function: wrong by
construction, so Proven (an empty body is not run separately — in Python it behaves exactly
like ``return None``).

Generators are skipped (a stub changes what kind of function they are). Fixes that change no
Python function report that the check doesn't apply.
"""

from __future__ import annotations

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants import python as py
from rlgate.variants.engine import VariantClass
from rlgate.variants.source import changed_python, diff_variant, with_rest_of_fix

MAX_FUNCTIONS = 15      # deterministic cap: the first N changed functions, in file order


@register
class StubProbe(Probe):
    meta = ProbeMeta(
        id="grader_strength.stub_probe",
        family="grader_strength",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="A function the fix changes can be replaced by a stub and the grader still passes.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        funcs = [(fc, s) for fc, s in changed_python(task, limit=MAX_FUNCTIONS)
                 if not s.generator]
        if not funcs:
            raise NotApplicable("the fix changes no Python function")
        # a function whose every changed line sits in a hunk already proven untested (its
        # removal passes) needs no stub: the revert probe has reported it
        bad = ctx.variants.unguarded_lines()
        funcs = [(fc, s) for fc, s in funcs
                 if not _covered(fc.added, s, bad.get(fc.path, set()))]
        jobs = []                            # (function, stub name, body, variant)
        for fc, span in funcs:
            assert fc.gold is not None
            for name, body in py.STUBS:
                v = diff_variant(task, with_rest_of_fix(task, {fc.path: py.stub(fc.gold, span,
                                                                        body)}),
                                 origin=f"{fc.path}:{span.qualname}", operator=f"stub:{name}")
                if v is not None:
                    jobs.append((fc, span, name, body, v))
        results = ctx.variants.run([j[4] for j in jobs])
        findings: list[Finding] = []
        done: set[str] = set()
        for (fc, span, name, body, v), r in zip(jobs, results, strict=True):
            key = f"{fc.path}:{span.qualname}"
            if key in done or not (r.is_exploit and r.klass is VariantClass.DISCRIMINATED):
                continue
            done.add(key)                    # one stub that passes is enough per function
            rule = rules.get("RG102")
            replay = repro.record(task, "RG102", patch=v.patch_text, expect="pass",
                                  cmd=ctx.variants.grader_cmd(),
                                  note=f"apply the fix with {span.qualname} stubbed "
                                       f"({name}) and grade")
            findings.append(Finding(
                probe_id=self.meta.id, rule_id="RG102",
                title=f"{rule.title}: {span.qualname}",
                severity=rule.severity, verdict=VerdictTier.CONFIRMED,
                location=f"{fc.path}:{span.def_line}",
                evidence=Evidence(
                    kind="reproduced_exploit",
                    detail=f"with {span.qualname} in {fc.path} replaced by `{body[0]}` "
                           f"and the rest of the fix in place, the grader still scores "
                           f"pass (reward {r.reward})",
                    replay=replay),
                fix_hint=f"Add a test that calls {span.qualname} and checks what it "
                         "returns or does."))
        return findings


def _covered(added: frozenset[int], span, bad: set[int]) -> bool:
    mine = {n for n in added if span.def_line <= n <= span.end}
    return bool(mine) and mine <= bad
