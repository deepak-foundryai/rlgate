"""L1 verdict channel — equality override. Every Python function the reference fix changes
(that returns a value) is made to return an object that equals anything: ``__eq__`` always
True, ``__ne__`` False, a constant ``__hash__``, truthy, and every attribute, item or call
returns itself. The rest of the fix stays in place. If the grade passes, the tests compare
with ``==`` and never pin the type or a serialised value. Proven.

All functions are replaced together first (one grading run); when that fails, each is tried
alone, so a function whose callers need its real value doesn't hide the others.

A pass proves the equality hole only if equality is what let it through. The same functions
returning an object equal to NOTHING are graded as a control: if that passes too, the tests
never compare the value (the function isn't called, or its result is thrown away), and
nothing is reported.
"""

from __future__ import annotations

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants import python as py
from rlgate.variants.engine import VariantClass, pmap
from rlgate.variants.source import (
    FileChange,
    FuncSpan,
    changed_python,
    diff_variant,
    with_rest_of_fix,
)

MAX_FUNCTIONS = 15
MAX_SINGLE = 8


@register
class EqualityOverride(Probe):
    meta = ProbeMeta(
        id="forgery.equality_override",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="Functions that return an always-equal object still score a pass.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        funcs = [(fc, s) for fc, s in changed_python(task, limit=MAX_FUNCTIONS)
                 if s.returns_value and not s.generator]
        if not funcs:
            raise NotApplicable("the fix changes no Python function that returns a value")
        hit = self._try(task, ctx, funcs)
        if hit is not None or len(funcs) == 1:
            return [hit] if hit is not None else []
        singles = pmap(lambda one: self._try(task, ctx, [one]), funcs[:MAX_SINGLE],
                       ctx.variants.workers)
        return [f for f in singles if f is not None]

    def _variant(self, task: Task, funcs: list[tuple[FileChange, FuncSpan]], body, op: str):
        texts: dict[str, str] = {}
        for fc, s in sorted(funcs, key=lambda x: -x[1].body_start):  # bottom-up per file
            assert fc.gold is not None
            texts[fc.path] = body(texts.get(fc.path, fc.gold), s)
        names = ", ".join(s.qualname for _, s in funcs)
        return diff_variant(task, with_rest_of_fix(task, texts), origin=names, operator=op)

    def _passes(self, ctx: ProbeContext, v) -> tuple[bool, float]:
        r = ctx.variants.score(v)
        return r.is_exploit and r.klass is VariantClass.DISCRIMINATED, r.reward

    def _try(self, task: Task, ctx: ProbeContext,
             funcs: list[tuple[FileChange, FuncSpan]]) -> Finding | None:
        names = ", ".join(s.qualname for _, s in funcs)
        v = self._variant(task, funcs, py.always_equal, "always_equal")
        if v is None:
            return None
        passed, reward = self._passes(ctx, v)
        if not passed:
            return None
        control = self._variant(task, funcs, py.never_equal, "never_equal")
        if control is None or self._passes(ctx, control)[0]:
            return None                    # passes whatever it returns: not an equality hole
        rule = rules.get("RG204")
        fc, s = funcs[0]
        replay = repro.record(task, "RG204", patch=v.patch_text, expect="pass",
                              cmd=ctx.variants.grader_cmd(),
                              note=f"apply the fix with {names} returning an always-equal "
                                   "object and grade")
        return Finding(
            probe_id=self.meta.id, rule_id="RG204", title=rule.title,
            severity=rule.severity, verdict=VerdictTier.CONFIRMED,
            location=f"{fc.path}:{s.def_line}",
            evidence=Evidence(
                kind="reproduced_exploit",
                detail=f"with {names} returning an object whose __eq__ is always True, the "
                       f"grader still scores pass (reward {reward}); returning one equal to "
                       "nothing fails, so the tests compare the value only with ==",
                replay=replay))
