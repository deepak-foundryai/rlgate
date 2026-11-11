"""Proof records: every Proven finding from execution can be re-run with ``rlgate repro``.

When a probe proves something by running the grader (a revert variant that still passes, a
forged conftest that fakes a pass, a gold run that fails), it records exactly what it ran: the
patch applied on the pristine workspace, any setup commands, the grader command, and which
outcome demonstrates the defect. The record is content-addressed (same inputs → same id) and
stored under ``$RLGATE_STATE_DIR`` (default ``./.rlgate``)::

    .rlgate/repro/<task-id>/<proof-id>.json

``rlgate repro <rule> <task> [--proof ID]`` loads the task, applies the record in a fresh
sandbox, re-grades, and says whether the defect reproduced. Recording never fails an audit:
if the state dir isn't writable the finding simply has no repro command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlgate.core.grade import GradeResult
    from rlgate.core.task import Task
    from rlgate.sandbox.base import Sandbox

STATE_ENV = "RLGATE_STATE_DIR"


@dataclass
class ProofRecord:
    id: str
    rule_id: str
    task_id: str
    origin: str | None
    patch: str | None               # applied on the pristine workspace (None = unchanged)
    expect: str                     # "pass" | "fail": the grade outcome that shows the defect
    cmd: str | None = None          # grader command used (None = the task's full grader)
    setup: list[str] = field(default_factory=list)   # shell commands run before grading
    note: str = ""                  # one line: what this proof does
    isolated: bool = False          # replay on a dedicated machine (payload writes outside
                                    # the workspace)
    oracle: bool = False            # grade the reference solution SCRIPT's output instead of
                                    # a patch (script-oracle tasks)

    def __post_init__(self) -> None:
        if self.expect not in ("pass", "fail"):
            raise ValueError(f"expect must be 'pass' or 'fail', got {self.expect!r}")


def state_dir() -> Path:
    return Path(os.environ.get(STATE_ENV, ".rlgate"))


def _task_dir(task_id: str) -> Path:
    return state_dir() / "repro" / re.sub(r"[^A-Za-z0-9._-]+", "_", task_id)


def _proof_id(rule_id: str, task_id: str, patch: str | None, expect: str, cmd: str | None,
              setup: list[str]) -> str:
    key = json.dumps([rule_id, task_id, patch, expect, cmd, setup])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def record(task: Task, rule_id: str, *, patch: str | None, expect: str,
           cmd: str | None = None, setup: list[str] | tuple[str, ...] = (),
           note: str = "", isolated: bool = False, oracle: bool = False) -> str | None:
    """Store a proof and return the ``rlgate repro`` command for it (None if not storable)."""
    setup = list(setup)
    rec = ProofRecord(
        id=_proof_id(rule_id, task.id, patch, expect, cmd, setup + (["<oracle>"] if oracle
                                                                     else [])),
        rule_id=rule_id, task_id=task.id, origin=task.origin,
        patch=patch, expect=expect, cmd=cmd, setup=setup, note=note, isolated=isolated,
        oracle=oracle,
    )
    try:
        d = _task_dir(task.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec.id}.json").write_text(json.dumps(asdict(rec), indent=2))
    except OSError:
        return None
    return command(task, rule_id, rec.id)


def command(task: Task, rule_id: str, proof_id: str) -> str:
    target = shlex.quote(task.origin or task.id)
    return f"rlgate repro {rule_id} {target} --proof {proof_id}"


def find(task_id: str, rule_id: str | None = None,
         proof_id: str | None = None) -> list[ProofRecord]:
    d = _task_dir(task_id)
    if not d.is_dir():
        return []
    out: list[ProofRecord] = []
    for f in sorted(d.glob("*.json")):
        try:
            rec = ProofRecord(**json.loads(f.read_text()))
        except (OSError, ValueError, TypeError):
            continue
        if rule_id and rec.rule_id != rule_id:
            continue
        if proof_id and not rec.id.startswith(proof_id):
            continue
        out.append(rec)
    return out


@dataclass
class ReplayResult:
    grade: GradeResult
    setup_failed: list[str]

    def reproduced(self, rec: ProofRecord) -> bool:
        return self.grade.passed if rec.expect == "pass" else not self.grade.passed


def replay(task: Task, rec: ProofRecord, sandbox: Sandbox) -> ReplayResult:
    """Apply ``rec`` on a fresh overlay of the pristine workspace and re-grade."""
    from rlgate.core.grade import run_grader
    from rlgate.core.payload import open_payload

    failed: list[str] = []
    if rec.oracle:
        if not hasattr(sandbox, "oracle_overlay"):
            raise ValueError("this proof grades the solution script; it needs the backend "
                             "the audit used (daytona/docker)")
        ovl = sandbox.oracle_overlay()          # type: ignore[attr-defined]
    else:
        # the same path the probe took: setup commands run where the agent works
        ovl, failed = open_payload(sandbox, rec.patch, rec.setup, isolated=rec.isolated)
    with ovl:
        g = run_grader(ovl, task, rec.cmd)
    return ReplayResult(g, failed)
