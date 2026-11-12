"""L0 — the submission is graded apart from the agent's environment.

A run in the agent's environment leaves three kinds of trace outside its submission: a file in
/tmp, a file in the home directory, and a background process. The work is then handed to the
grader exactly as the harness does, and the grading environment is checked for each trace.
Any trace that survives is proof that changes outside the submission reach the grader (RG010).
"""

from __future__ import annotations

import shlex
import uuid

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task


@register
class Carryover(Probe):
    meta = ProbeMeta(
        id="hygiene.carryover",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.CONTAINER}),
        doc="Files and processes the agent leaves outside its submission must not reach the "
            "grader.",
    )

    def applicable(self, task: Task) -> bool:
        return super().applicable(task) and task.staged

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        run_as_agent = getattr(ctx.sandbox, "agent_overlay", None)
        if run_as_agent is None:
            return []
        tag = f"rlgate-carry-{uuid.uuid4().hex[:10]}"
        traces = {f"/tmp/{tag}": "a file in /tmp",
                  f"$HOME/.{tag}": "a file in the home directory"}
        plant = "; ".join(f"echo {tag} > {p}" for p in traces) + \
            f"; (nohup sh -c 'sleep 100000; : {tag}' >/dev/null 2>&1 &) ; true"
        found: list[str] = []
        with run_as_agent(plant) as ovl:
            for path, what in traces.items():
                if ovl.exec(f"test -e {path}", timeout=60).exit_code == 0:
                    found.append(what)
            # `[r]lgate-carry-…` matches the planted process but not this command's own
            # wrappers (Linux pgrep -f matches its `timeout … sh -c pgrep …` ancestors)
            pattern = shlex.quote(f"[{tag[0]}]{tag[1:]}")
            if ovl.exec(f"pgrep -f {pattern} >/dev/null", timeout=60).exit_code == 0:
                found.append("a background process")
        if not found:
            return []
        return [Finding(
            probe_id=self.meta.id,
            rule_id="RG010",
            title="Changes outside the submission reach the grader",
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED,
            location="grading environment",
            evidence=Evidence(
                kind="reproduced_exploit",
                detail=f"after a run left {_and(found)} outside its submission, "
                       f"{'it was' if len(found) == 1 else 'they were'} still there when "
                       "grading started",
                replay=f"rlgate check {task.origin or task.id} --only works",
            ),
        )]


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
