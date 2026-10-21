"""Orchestration: ingest → baseline (fail-fast) → applicable probes (capability-gated) →
report. Probes gate on capabilities only; the pipeline never branches on benchmark identity."""

from __future__ import annotations

from dataclasses import dataclass

from rlgate.baseline.invariant import establish
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Finding
from rlgate.core.registry import discover
from rlgate.core.task import Task
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.local import LocalSandbox
from rlgate.variants.engine import VariantEngine


@dataclass
class AuditResult:
    task_id: str
    baseline_sane: bool
    findings: list[Finding]


def audit(task: Task, sandbox: Sandbox | None = None, k: int = 5,
          only: set[str] | None = None) -> AuditResult:
    sandbox = sandbox or LocalSandbox(task.repo_dir)
    sandbox.build()

    baseline = establish(task, sandbox, k=k)
    ctx = ProbeContext(
        sandbox=sandbox,
        baseline=baseline,
        variants=VariantEngine(task, sandbox, baseline),
    )

    findings: list[Finding] = []
    for pid, cls in discover().items():
        if only and pid not in only:
            continue
        probe = cls()
        if not probe.applicable(task):
            continue
        # Fail-fast: when the baseline is broken, skip probes that assume it's sane.
        if not baseline.sane and probe.meta.family in {"grader_strength", "reward_shape"}:
            continue
        findings.extend(probe.run(task, ctx))

    if not baseline.sane:
        findings.insert(0, _baseline_finding(baseline))
    return AuditResult(task.id, baseline.sane, findings)


def _baseline_finding(baseline) -> Finding:
    from rlgate.core.finding import Evidence, Severity, VerdictTier
    detail = f"oracle={baseline.oracle} nop={baseline.nop} flaky={sorted(baseline.flaky_checks)}"
    return Finding(
        probe_id="baseline.invariant",
        title="baseline not sane (oracle!=1.0 / nop!=0.0 / flaky)",
        severity=Severity.CRITICAL,
        verdict=VerdictTier.CONFIRMED,
        evidence=Evidence(kind="reproduced_exploit", detail=detail),
        fix_hint="the environment is broken before hacking is even discussed; fix this first.",
    )
