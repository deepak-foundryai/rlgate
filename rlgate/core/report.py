"""Turn findings into an EQS + KEEP/FIX/DROP triage + JSON/Markdown."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum

from rlgate.core.finding import Finding, Severity, VerdictTier


class Triage(Enum):
    KEEP = "KEEP"
    FIX = "FIX"
    DROP = "DROP"


def triage(findings: list[Finding], baseline_sane: bool) -> Triage:
    if not baseline_sane:
        return Triage.DROP
    if any(f.severity >= Severity.CRITICAL for f in findings):
        return Triage.DROP
    if findings:
        return Triage.FIX
    return Triage.KEEP


def eqs(findings: list[Finding], baseline_sane: bool) -> float:
    """Environment Quality Score in [0,1]: 1.0 clean, down-weighted by severity."""
    if not baseline_sane:
        return 0.0
    penalty = sum(0.1 * int(f.severity) for f in findings)
    return max(0.0, 1.0 - penalty)


def to_json(task_id: str, findings: list[Finding], baseline_sane: bool) -> str:
    payload = {
        "task": task_id,
        "eqs": eqs(findings, baseline_sane),
        "triage": triage(findings, baseline_sane).value,
        "baseline_sane": baseline_sane,
        "findings": [_finding_json(f) for f in findings],
    }
    return json.dumps(payload, indent=2)


def _finding_json(f: Finding) -> dict:
    d = asdict(f)
    d["severity"] = f.severity.name
    d["verdict"] = f.verdict.value
    d["evidence"]["artifacts"] = [str(p) for p in f.evidence.artifacts]
    return d


def to_markdown(task_id: str, findings: list[Finding], baseline_sane: bool) -> str:
    tier = {VerdictTier.CONFIRMED: "[CONFIRMED]", VerdictTier.REVIEW: "[REVIEW]",
            VerdictTier.DEFERRED: "[DEFERRED]"}
    lines = [
        f"# rlgate audit — {task_id}",
        (f"**EQS {eqs(findings, baseline_sane):.2f} · "
         f"{triage(findings, baseline_sane).value}** "
         f"· baseline {'OK' if baseline_sane else 'BROKEN'}"),
        "",
    ]
    if not findings:
        lines.append("_No findings._")
    for f in sorted(findings, key=lambda x: x.severity, reverse=True):
        lines += [
            f"## {tier[f.verdict]} {f.title}  ({f.severity.name})",
            f"- probe: `{f.probe_id}`" + (f" · at `{f.location}`" if f.location else ""),
            f"- evidence: {f.evidence.detail}",
        ]
        if f.evidence.replay:
            lines.append(f"- replay: `{f.evidence.replay}`")
        if f.fix_hint:
            lines.append(f"- fix: {f.fix_hint}")
        lines.append("")
    return "\n".join(lines)
