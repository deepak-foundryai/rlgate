"""Shared machinery for verdict-channel probes: plant a payload, run the real grader, and
report a Proven finding when a workspace that does NOT solve the task scores a pass.

Every forgery probe is the same loop: build a payload (a patch on the pristine workspace,
optionally shell commands the agent runs), grade with the task's FULL grader command — the
real grading path, never the variant engine's subset — and, if it passes, record a proof and
emit the finding. A probe subclasses ``PayloadProbe`` and only declares ``payloads``.

Where the payload runs matters. Commands (``setup``) are what the AGENT would run, so on a
backend that can play the agent (``agent_overlay``: the task runs in its own images) they run
in a fresh agent environment, and the harness's own hand-over decides what reaches the
grader — a separate verifier receives only the declared artifacts, exactly as in training. On
the copy-a-directory backends the agent and the grader share one environment, so the commands
run there before grading.

Attribution: a forged pass only proves forgery when doing nothing genuinely fails. If the
do-nothing baseline already passes, the grader is broken in a more basic way (RG002) and
payload probes report nothing.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rlgate.core import repro, rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, VerdictTier
from rlgate.core.grade import GradeResult, run_grader
from rlgate.core.probe import NotApplicable, Probe
from rlgate.core.task import Task
from rlgate.sandbox._patch import PatchConflict
from rlgate.sandbox.errors import IsolationUnavailable

if TYPE_CHECKING:
    from rlgate.sandbox.base import Overlay, Sandbox


@dataclass(frozen=True)
class Payload:
    name: str                               # short, stable, e.g. "root-conftest"
    patch: str | None                       # applied on the pristine workspace
    what: str                               # one line for the finding: what was planted
    setup: tuple[str, ...] = ()             # shell commands the agent runs before grading
    location: str | None = None             # file the payload plants/edits
    isolated: bool = False                  # writes outside the workspace (site-packages,
                                            # /tmp, …) → needs a machine of its own


def open_payload(sandbox: Sandbox, patch: str | None, setup: Iterable[str] = (), *,
                 isolated: bool = False) -> tuple[Overlay, list[str]]:
    """An overlay holding the agent's work: ``patch`` applied and ``setup`` run where the
    agent works. Returns (overlay, setup commands that failed). Raises ``PatchConflict`` when
    the patch doesn't apply and ``IsolationUnavailable`` when the backend can't isolate."""
    setup = list(setup)
    agent = getattr(sandbox, "agent_overlay", None)
    if setup and agent is not None:
        cmd = "\n".join(f"(\n{c}\n)" for c in setup)   # newlines: heredocs stay intact
        return agent(cmd, patch=patch), []
    ovl = sandbox.overlay(patch, isolated=isolated)
    failed = [c for c in setup if ovl.exec(c).exit_code != 0]
    return ovl, failed


class PayloadProbe(Probe):
    rule_id: ClassVar[str]
    fix: ClassVar[str | None] = None        # overrides the rule's default fix when set

    def payloads(self, task: Task, ctx: ProbeContext) -> Iterable[Payload]:
        raise NotImplementedError

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        if ctx.baseline.nop >= task.grader.pass_reward:
            return []  # do-nothing already passes → RG002, not attributable to the payload
        from rlgate.variants.engine import pmap

        def grade(p: Payload) -> GradeResult | IsolationUnavailable | None:
            try:
                return self._grade(task, ctx, p)
            except IsolationUnavailable as e:
                return e

        plants = list(self.payloads(task, ctx))
        workers = getattr(ctx.variants, "workers", 1) if ctx.variants is not None else 1
        findings: list[Finding] = []
        tried, skipped, isolation_msg = len(plants), 0, ""
        for p, g in zip(plants, pmap(grade, plants, workers), strict=True):
            if isinstance(g, IsolationUnavailable):
                skipped += 1
                isolation_msg = str(g)
            elif g is not None and g.passed:
                findings.append(self._finding(task, p, g))
        if tried and skipped == tried:
            raise NotApplicable(isolation_msg)
        if skipped and not findings:      # don't claim a pass for plants that never ran
            raise NotApplicable(f"{skipped} of {tried} plants write outside the workspace; "
                                f"{_lc(isolation_msg)}")
        return findings

    def _grade(self, task: Task, ctx: ProbeContext, p: Payload) -> GradeResult | None:
        try:
            overlay, _ = open_payload(ctx.sandbox, p.patch, p.setup, isolated=p.isolated)
        except PatchConflict:
            return None  # payload doesn't apply to this workspace → nothing to report
        with overlay:
            return run_grader(overlay, task)

    def _finding(self, task: Task, p: Payload, g: GradeResult) -> Finding:
        rule = rules.get(self.rule_id)
        replay = repro.record(task, self.rule_id, patch=p.patch, expect="pass",
                              setup=p.setup, note=p.what, isolated=p.isolated)
        return Finding(
            probe_id=self.meta.id,
            rule_id=self.rule_id,
            title=rule.title,
            severity=rule.severity,
            verdict=VerdictTier.CONFIRMED,
            location=p.location,
            evidence=Evidence(
                kind="reproduced_exploit",
                detail=f"{p.what} With no fix applied, the grader scored {g.reward} "
                       f"(exit {g.exit_code}). Payload: {p.name}.",
                replay=replay,
            ),
            fix_hint=self.fix or rule.fix,
        )


def _lc(msg: str) -> str:
    """The isolation message's advice ("…; it needs --backend docker or daytona")."""
    return msg.rsplit("; ", 1)[-1] if "; " in msg else msg


def write_file(path: str, content: str) -> str:
    """A shell command that writes ``content`` to ``path`` (parents created), byte-exact."""
    tag = "RLGATE_EOF"
    while tag in content:
        tag += "_"
    q = shlex.quote(path)
    body = content if content.endswith("\n") else content + "\n"
    return f"mkdir -p \"$(dirname {q})\" && cat > {q} <<'{tag}'\n{body}{tag}"


# Every Python the grader might use: on PATH, in /opt|/usr/local venvs, in the workspace.
PYTHONS = ("{ command -v python3; command -v python; ls -1 /usr/local/bin/python3 "
           "/usr/bin/python3 /opt/*/bin/python3 /opt/*/*/bin/python3 2>/dev/null; "
           "find / -maxdepth 5 -path '*/.venv/bin/python3' -not -path '/proc/*' 2>/dev/null; "
           "} | sort -u")


def into_site_packages(name: str, content: str) -> str:
    """A shell command that writes ``name`` into the site-packages of every Python the
    grader might use (each interpreter's ``purelib``)."""
    write = write_file("$sp/" + name, content).replace(shlex.quote("$sp/" + name),
                                                        f'"$sp/{name}"')
    return (f"for py in $({PYTHONS}); do "
            "sp=$(\"$py\" -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])' "
            "2>/dev/null) || continue; [ -d \"$sp\" ] || continue; "
            f"{write}\n"
            "done")
