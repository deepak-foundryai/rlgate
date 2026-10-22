"""§3.1 — test-infrastructure files the gold solution never touches (DeepSWE's "recorded only"
list: conftest.py, sitecustomize.py, pytest.ini, tox.ini, setup.cfg, Go TestMain). Frameworks
auto-load these before any test, so a candidate patch that edits one can hijack collection or
reporting and fake a pass. If the grader doesn't restore them from a known-good copy, they are
a forgeable verdict channel. We can't see the grader's restore logic statically → REVIEW.
"""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task

_DANGER_NAMES = {
    "conftest.py", "sitecustomize.py", "usercustomize.py",
    "pytest.ini", "tox.ini", "setup.cfg",
}
_PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
_MAX_READ = 200_000


def _reads(p: Path, needle: str) -> bool:
    try:
        return needle in p.read_text(errors="ignore")[:_MAX_READ]
    except OSError:
        return False


@register
class UnrestoredTestInfra(Probe):
    meta = ProbeMeta(
        id="forgery.unrestored_test_infra",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="Test-infra files the gold fix never touches can hijack collection/reporting.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        gold_files = {pf.path for pf in task.gold_patch.patchset}
        root = task.repo_dir
        findings: list[Finding] = []
        for rel, kind in self._candidates(root):
            if rel in gold_files:
                continue  # the reference touches it → in scope, not a hidden channel
            findings.append(Finding(
                probe_id=self.meta.id,
                title=f"unrestored test-infra ({kind}): {rel}",
                severity=Severity.HIGH,
                verdict=VerdictTier.REVIEW,
                location=rel,
                evidence=Evidence(
                    kind="code_line",
                    detail=f"{rel} ({kind}) is auto-loaded by the test runner and the gold "
                           "solution never touches it; a candidate patch can edit it to fake a "
                           "pass unless the grader restores it from a known-good copy.",
                    replay=f"rlgate audit {task.id} --probe {self.meta.id}",
                ),
                fix_hint="overwrite all test-infra from a read-only reference before scoring; "
                         "reject (don't record) patches that touch files the gold never does.",
            ))
        return findings

    def _candidates(self, root: Path):
        for p in root.rglob("*"):
            if any(part in _PRUNE for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if p.name in _DANGER_NAMES:
                yield rel, p.name
            elif p.name.endswith("_test.go") and _reads(p, "func TestMain("):
                yield rel, "TestMain"
