"""How the task is run, read from what its loader recorded. No execution.

Two checks, both from task fields any loader can fill (never from the benchmark's name):

  - verifier isolation (RG004): the workspace lives in a container but the grader has no
    environment of its own, so it grades inside the container the agent used;
  - harness compatibility (RG605): defects in the task files that its harness would trip
    on too (``meta["format_issues"]``). Shapes the harness runs fine but rlgate can't yet
    reproduce (``meta["unsupported"]`` alone) are NOT findings: they only limit the task to
    a quick check, and the check output says so.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task


def _config_file(task: Task) -> str | None:
    from rlgate.core.waivers import task_root

    root = task_root(task.origin or task.repo_dir)
    for name in ("task.toml", "task.rlgate.yaml"):
        if (root / name).is_file():
            return name
    return None


@register
class VerifierIsolation(Probe):
    meta = ProbeMeta(
        id="hygiene.verifier_isolation",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        doc="The grader runs in the container the agent used (no separate verifier).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        if task.workspace_env is None or task.grader.env is not None:
            return []
        loc = _config_file(task)
        return [Finding(
            probe_id=self.meta.id, rule_id="RG004",
            title="The grader runs in the agent's environment",
            severity=Severity.MEDIUM, verdict=VerdictTier.REVIEW, location=loc,
            evidence=Evidence(kind="code_line", detail=(
                "The agent's container is reused for grading: the tests are copied in after "
                "the agent finishes, and whatever it left behind is still running.")),
        )]


@register
class HarborCompat(Probe):
    meta = ProbeMeta(
        id="hygiene.harbor_compat",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        doc="The task's harness can't run it as written (format gaps, unsupported shapes).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        loc = _config_file(task)
        out = []
        for why in task.meta.get("format_issues", []) or []:
            if why.startswith("No solution/solve.sh"):
                # RL training data often ships no reference: a warning, never a stop. The
                # checks that need one show as "doesn't apply"; every other check still runs
                out.append(self._f("No solution/solve.sh: the reference solution can't be "
                                   "run, so nobody can show the task is solvable. Checks "
                                   "that need a reference are skipped; the rest still run.",
                                   loc, Severity.LOW))
                continue
            out.append(self._f(why.rstrip(".") + ".", loc))
        return out

    def _f(self, detail: str, loc: str | None, severity: Severity = Severity.MEDIUM
           ) -> Finding:
        return Finding(
            probe_id=self.meta.id, rule_id="RG605",
            title="The task can't run the way its harness runs it",
            severity=severity, verdict=VerdictTier.CONFIRMED, location=loc,
            evidence=Evidence(kind="code_line", detail=detail),
        )
