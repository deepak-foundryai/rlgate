"""What every probe returns. A finding is a reproduced exploit or a concrete code location —
never a threshold on a score."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class VerdictTier(Enum):
    CONFIRMED = "confirmed"   # execution-proven: a reproduced exploit or a concrete code fact
    REVIEW = "review"         # detected deterministically; fairness needs NL/human adjudication
    DEFERRED = "deferred"     # out of the deterministic core's scope; needs the LLM layer


@dataclass
class Evidence:
    kind: str                      # "reproduced_exploit"|"code_line"|"variant"|"perms"|"diff"
    detail: str
    replay: str | None = None      # exact command to reproduce
    artifacts: list[Path] = field(default_factory=list)


@dataclass
class Finding:
    probe_id: str
    title: str
    severity: Severity
    verdict: VerdictTier
    evidence: Evidence
    location: str | None = None    # file:line the finding anchors to
    fix_hint: str | None = None    # the actionable remediation (FIX, not just DROP)
