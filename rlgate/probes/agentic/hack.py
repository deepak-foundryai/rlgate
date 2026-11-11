"""L4 hack agent (``--deep-review``, full run, docker or daytona): short agent runs try to reach
full reward without solving the task; every claim is re-checked by execution
(``agentic/hack.py``).

  RG215  a pass that needs changes outside the source (tests, config, environment): Proven
  RG111  the source changes alone pass, and the agent's check proves them wrong: Proven
  RG113  the source changes alone pass, but nothing proves them wrong: needs judgment
  RG308  a command reveals the reference fix's added lines: Proven

A saved hack is replayed first on a re-check; while it still works the task stays broken and
the agent is not re-run (no LLM spend). Never runs on the local backend: the agent runs
attacker commands, which must stay inside a container.
"""

from __future__ import annotations

import re

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import CouldNotJudge, NotApplicable, Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants.python import is_test_file

HACK = "hack agent"              # repro notes start with this: how saved hacks are found
_TEST_INFRA = re.compile(
    r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|sitecustomize\.py|"
    r"usercustomize\.py|[^/]+\.pth|(jest|vitest|karma|playwright)\.config\.[cm]?[jt]s|"
    r"\.mocharc(\.[a-z]+)?|phpunit\.xml(\.dist)?|noxfile\.py)$")


def is_test_infra(path: str, graded: list[str]) -> bool:
    """A file a solution's source never needs: tests, test config, interpreter hooks."""
    return (is_test_file(path) or bool(_TEST_INFRA.search(path)) or "/tests/" in f"/{path}"
            or any(path == d or path.startswith(d + "/") for d in graded))


@register
class HackProbe(Probe):
    meta = ProbeMeta(id="agentic.hack", family="agentic", kind=ProbeKind.GENERIC,
                     severity=Severity.CRITICAL, needs_live=True, needs_llm=True,
                     doc="An agent tries to reach full reward without solving the task; "
                         "every pass is replayed and proven or dropped.")
    note = ""

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        s = ctx.review
        if s is None:
            raise NotApplicable("runs with --deep-review")
        sb = ctx.sandbox
        if sb is None:
            raise NotApplicable("needs a full run: the agent runs commands in the sandbox")
        if type(sb).__name__ == "LocalSandbox":
            raise NotApplicable("the hack agent runs attacker commands, so it needs a "
                                "container: --backend docker or daytona")
        if not (task.instruction or "").strip():
            raise NotApplicable("the task has no statement")
        if ctx.baseline is not None and ctx.baseline.nop >= task.grader.pass_reward:
            return []              # doing nothing passes: RG002 says it all

        saved = self._saved(task, sb)
        if saved:
            self.note = "a saved hack still works, so the agent was not re-run"
            return saved

        runs = s.hack(task, ctx)
        self.note = "; ".join(r.note for r in runs)
        if not any(r.submissions or r.lookups for r in runs) and not any(r.finished
                                                                          for r in runs):
            raise CouldNotJudge("no hack run finished: " + "; ".join(
                r.summary or r.note for r in runs))
        out: list[Finding] = []
        seen: set[tuple[str, ...]] = set()
        before = None
        for r in runs:
            for sub in r.submissions:
                key = tuple(sub.history)
                if not sub.passed or key in seen:
                    continue
                seen.add(key)
                if before is None:
                    before = self._manifest(sb)
                f = self._prove(task, ctx, r.slice, sub, before)
                if f is not None:
                    out.append(f)
            for lk in r.lookups:
                f = self._lookup(task, ctx, lk)
                if f is not None:
                    out.append(f)
        return out

    # -- saved hacks -----------------------------------------------------------------------

    def _saved(self, task: Task, sb) -> list[Finding]:
        out = []
        for rule_id in ("RG215", "RG111"):
            for rec in repro.find(task.id, rule_id):
                if not rec.note.startswith(HACK):
                    continue
                try:
                    rr = repro.replay(task, rec, sb)
                except Exception:              # noqa: BLE001, S112 - a stale proof; re-hunt
                    continue
                if rr.reproduced(rec):
                    out.append(self._finding(
                        rule_id, None, f"A saved hack still passes (reward "
                        f"{rr.grade.reward}): {rec.note.removeprefix(HACK + ': ')}",
                        repro.command(task, rule_id, rec.id)))
        return out

    # -- proofs ------------------------------------------------------------------------------

    @staticmethod
    def _manifest(sb) -> dict[str, str]:
        from rlgate.agentic.hack import manifest
        with sb.overlay(None, isolated=True) as ovl:
            return manifest(ovl)

    def _prove(self, task: Task, ctx: ProbeContext, slice_: str, sub, before
               ) -> Finding | None:
        from rlgate.agentic.hack import workspace_changes
        from rlgate.core.grade import cached_grade
        from rlgate.sandbox._patch import PatchConflict
        from rlgate.variants.source import unified_diff

        sb = ctx.sandbox
        graded = [d.strip("/") for _, d in task.grader.inject]
        files, binary = workspace_changes(sb, sub.history, before)
        source = {p: c for p, c in files.items() if not is_test_infra(p, graded)}
        infra = sorted(p for p in files if is_test_infra(p, graded)) + binary
        patch = unified_diff(task.repo_dir, source) if source else ""
        claim = sub.claim.strip().rstrip(".") or "no claim given"
        if patch:
            try:
                clean = cached_grade(sb, task, patch, deterministic=ctx.baseline is not None
                                     and not ctx.baseline.flaky_checks)
            except PatchConflict:
                self.note += "; a passing hack's source changes didn't apply cleanly"
                return None
            clean_passed, clean_reward = clean.passed, clean.reward
        else:
            clean_passed = ctx.baseline is not None and \
                ctx.baseline.nop >= task.grader.pass_reward
            clean_reward = ctx.baseline.nop if ctx.baseline is not None else 0.0

        if not clean_passed:
            replay = repro.record(task, "RG215", patch=None, expect="pass",
                                  setup=sub.history, isolated=True,
                                  note=f"{HACK}: {claim}")
            where = (f" It changed {', '.join(infra[:6])}"
                     f"{' and more' if len(infra) > 6 else ''}." if infra else
                     " Its changes outside the workspace are what made the difference.")
            return self._finding("RG215", infra[0] if infra else None, (
                f"A hack run ({slice_}) passed: {claim}.{where}\n"
                f"Its {len(sub.history)} commands, replayed in a fresh environment, score "
                f"{sub.reward}; its source changes alone score {clean_reward}, so the pass "
                "comes from the grading setup, not the code."), replay)

        proven, why = self._check(ctx, task, patch, sub.check)
        rule = "RG111" if proven else "RG113"
        replay = repro.record(task, rule, patch=patch or None, expect="pass",
                              note=f"{HACK}: {claim}")
        changed = ", ".join(sorted(source)[:6]) or "no source file"
        detail = (f"A hack run ({slice_}) passed with source changes only ({changed}): "
                  f"{claim}.\n{why}")
        return self._finding(rule, min(source) if source else None, detail, replay)

    @staticmethod
    def _check(ctx: ProbeContext, task: Task, patch: str, check: str) -> tuple[bool, str]:
        """Proof B: the agent's check fails on its code and passes on the reference."""
        from rlgate.sandbox._patch import PatchConflict

        check = (check or "").strip()
        if not check:
            return False, "The agent gave no check command, so nothing proves the code wrong."
        if task.gold_patch is None:
            return False, "There is no reference fix to validate the agent's check against."
        try:
            with ctx.sandbox.overlay(patch or None) as ovl:
                on_hack = ovl.exec(check, timeout=300)
            with ctx.sandbox.overlay(task.gold_patch.text) as ovl:
                on_ref = ovl.exec(check, timeout=300)
        except PatchConflict:
            return False, "The check could not be run (a patch didn't apply)."
        if on_hack.exit_code != 0 and on_ref.exit_code == 0:
            return True, (f"`{check}` fails on this code and passes on the reference fix, "
                          "so the code really is wrong.")
        what = ("passes on the hack too" if on_hack.exit_code == 0 else
                "fails on the reference fix too")
        return False, (f"The agent's check `{check}` {what}, so it doesn't prove the code "
                       "wrong; read the code.")

    def _lookup(self, task: Task, ctx: ProbeContext, lk) -> Finding | None:
        from rlgate.agentic.hack import lookup_matches, lookup_proven

        if task.gold_patch is None:
            return None
        with ctx.sandbox.agent_env() as ovl:
            r = ovl.exec(lk.command, timeout=300)
        found, of, examples = lookup_matches(r.stdout, task.gold_patch.text)
        if not lookup_proven(found, of):
            self.note += f"; a reported lookup showed {found} of {of} reference lines"
            return None
        shown = "\n".join(f"  {e}" for e in examples)
        return self._finding("RG308", None, (
            f"`{lk.command}`, run in a fresh copy of the agent's environment, prints {found} "
            f"of the reference fix's {of} added lines ({lk.what.strip().rstrip('.')}), "
            f"for example:\n{shown}"), None, kind="code_line")

    def _finding(self, rule_id: str, location: str | None, detail: str,
                 replay: str | None, kind: str = "reproduced_exploit") -> Finding:
        rule = rules.get(rule_id)
        proven = rule.proof is rules.Proof.PROVEN
        return Finding(probe_id=self.meta.id, rule_id=rule_id, title=rule.title,
                       severity=rule.severity,
                       verdict=VerdictTier.CONFIRMED if proven else VerdictTier.REVIEW,
                       location=location, evidence=Evidence(kind=kind, detail=detail,
                                                            replay=replay),
                       fix_hint=rule.fix)
