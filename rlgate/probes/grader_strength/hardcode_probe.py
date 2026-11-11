"""L1 coverage — hardcode probe. Return the expected outputs for the visible test inputs only;
the held-out tests must fail it.

1. Record: the reference fix runs with every changed Python function wrapped so each call
   logs its arguments and result — run on the tests the agent can SEE (the visible subset
   through the grader's ``select`` template; the full grader when every graded test is
   visible).
2. Hardcode: each function whose results are all plain literals becomes a lookup table of
   those calls (its real code never runs; an unseen input returns None).
3. Grade with the full graded set. A pass means no graded test uses an input the agent
   couldn't see: the agent can memorise answers instead of solving the task. Proven.

When the agent sees none of the graded tests there is nothing to memorise, and the check is
clean. Tests split between visible and held-out with no per-test report or subset command
can't be told apart, and the check says it doesn't apply.
"""

from __future__ import annotations

import shlex
import uuid

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.grade import run_grader
from rlgate.core.probe import NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.sandbox._patch import PatchConflict
from rlgate.variants import python as py
from rlgate.variants import visible
from rlgate.variants.engine import VariantClass, pmap
from rlgate.variants.source import (
    FileChange,
    FuncSpan,
    changed_python,
    diff_variant,
    with_rest_of_fix,
)

MAX_FUNCTIONS = 15
MAX_SINGLE = 8          # functions tried one at a time when all of them together fail


@register
class HardcodeProbe(Probe):
    meta = ProbeMeta(
        id="grader_strength.hardcode_probe",
        family="grader_strength",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="Answers recorded from the visible tests, served from a lookup table, still pass.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        funcs = [(fc, s) for fc, s in changed_python(task, limit=MAX_FUNCTIONS)
                 if py.wrappable(s)]
        if not funcs:
            raise NotApplicable("the fix changes no plain Python function that returns a value")
        sp = visible.split(task, ctx.baseline)
        if sp is None:
            raise NotApplicable("can't tell the visible tests from the held-out ones without "
                                "a per-test report")
        if sp.known and not sp.visible:
            return []                       # the agent sees no graded test: nothing to copy
        cmd = None                          # every graded test is visible: run them all
        if sp.known and sp.hidden:
            if not task.grader.select:
                raise NotApplicable("the grader has no command to run only the visible tests "
                                    "(grader.select)")
            cmd = task.grader.select.format(tests=" ".join(sp.visible))

        tables = self._record(task, ctx, funcs, cmd)
        usable: list[tuple[FileChange, FuncSpan, dict[str, str]]] = [
            (fc, s, t) for fc, s in funcs
            if (t := tables.get(s.qualname))]      # called, and every result is a literal
        if not usable:
            return []
        what = "the visible tests" if sp.hidden else "the tests (all visible to the agent)"

        combined = self._grade(task, ctx, usable)
        if combined is not None:
            return [self._finding(task, ctx, usable, combined, what, sp.hidden)]
        if len(usable) == 1:
            return []
        singles = usable[:MAX_SINGLE]
        graded = pmap(lambda one: self._grade(task, ctx, [one]), singles, ctx.variants.workers)
        return [self._finding(task, ctx, [one], v, what, sp.hidden)
                for one, v in zip(singles, graded, strict=True) if v is not None]

    # -- 1. record ---------------------------------------------------------------------

    def _record(self, task: Task, ctx: ProbeContext,
                funcs: list[tuple[FileChange, FuncSpan]],
                cmd: str | None) -> dict[str, dict[str, str] | None]:
        path = f"/tmp/rlgate-trace-{uuid.uuid4().hex[:12]}.jsonl"
        by_file: dict[str, list[FuncSpan]] = {}
        gold: dict[str, str] = {}
        for fc, s in funcs:
            assert fc.gold is not None
            by_file.setdefault(fc.path, []).append(s)
            gold[fc.path] = fc.gold
        files = {p: py.trace_appendix(gold[p], spans, path) for p, spans in by_file.items()}
        v = diff_variant(task, with_rest_of_fix(task, files), origin="trace",
                         operator="trace")
        if v is None:
            return {}
        try:
            ovl = ctx.sandbox.overlay(v.patch_text)
        except PatchConflict:
            return {}
        with ovl:
            run_grader(ovl, task, cmd)
            out = ovl.exec(f"cat {shlex.quote(path)} 2>/dev/null; rm -f {shlex.quote(path)}",
                           timeout=120).stdout
        return py.parse_trace(out)

    # -- 2/3. hardcode and grade -------------------------------------------------------

    def _grade(self, task: Task, ctx: ProbeContext,
               usable: list[tuple[FileChange, FuncSpan, dict[str, str]]]):
        by_file: dict[str, list[tuple[FuncSpan, dict[str, str]]]] = {}
        gold: dict[str, str] = {}
        for fc, s, table in usable:
            assert fc.gold is not None
            by_file.setdefault(fc.path, []).append((s, table))
            gold[fc.path] = fc.gold
        files = {p: py.hardcode(gold[p], items) for p, items in by_file.items()}
        names = "+".join(s.qualname for _, s, _ in usable)
        v = diff_variant(task, with_rest_of_fix(task, files), origin=names,
                         operator="hardcode")
        if v is None:
            return None
        r = ctx.variants.score(v)
        if r.is_exploit and r.klass is VariantClass.DISCRIMINATED:
            return v, r
        return None

    def _finding(self, task: Task, ctx: ProbeContext,
                 usable: list[tuple[FileChange, FuncSpan, dict[str, str]]], vr, what: str,
                 hidden: list[str]) -> Finding:
        v, r = vr
        rule = rules.get("RG104")
        names = ", ".join(s.qualname for _, s, _ in usable)
        calls = sum(len(t) for _, _, t in usable)
        replay = repro.record(task, "RG104", patch=v.patch_text, expect="pass",
                              cmd=ctx.variants.grader_cmd(),
                              note=f"apply the fix with {names} answering from a lookup "
                                   "table and grade")
        fc, span, _ = usable[0]
        held = (f"; none of the {len(hidden)} held-out tests uses another input" if hidden
                else "; there are no held-out tests")
        return Finding(
            probe_id=self.meta.id, rule_id="RG104", title=rule.title,
            severity=rule.severity, verdict=VerdictTier.CONFIRMED,
            location=f"{fc.path}:{span.def_line}",
            evidence=Evidence(
                kind="reproduced_exploit",
                detail=f"{names} replaced by a lookup table of the {calls} input(s) "
                       f"{what} use still scores pass (reward {r.reward}){held}",
                replay=replay),
            fix_hint=None)

