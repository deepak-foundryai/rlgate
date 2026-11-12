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
from rlgate.probes.test_quality._graded import collected_paths, grader_removes, runs_pytest

_DANGER_NAMES = {
    "conftest.py", "sitecustomize.py", "usercustomize.py",
    "pytest.ini", "tox.ini", "setup.cfg",
}
_PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
_MAX_READ = 200_000
# the ini file pytest takes its config from: the first of these at the root that has the
# section (pytest.ini always counts)
_INI_ORDER = (("pytest.ini", ""), ("pyproject.toml", "[tool.pytest.ini_options]"),
              ("tox.ini", "[pytest]"), ("setup.cfg", "[tool:pytest]"))
_INI = {"pytest.ini", "tox.ini", "setup.cfg"}


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
        assert task.gold_patch is not None         # requires GOLD_PATCH
        gold_files = {pf.path for pf in task.gold_patch.patchset}
        root = task.repo_dir
        pytest = runs_pytest(task)
        collected = collected_paths(task)
        inis = self._pytest_inis(root, collected)
        removed = grader_removes(task)
        findings: list[Finding] = []
        for rel, kind in self._candidates(root):
            if rel in gold_files:
                continue  # the reference touches it → in scope, not a hidden channel
            if removed.covers(rel):
                continue  # the grader deletes or restores it before the tests run
            if kind == "conftest.py" or kind in _INI:
                if pytest is False:
                    continue        # the grader runs another runner: pytest never loads it
                if kind in _INI and rel not in inis:
                    continue        # not a file pytest reads its config from
                if kind == "conftest.py" and collected is not None \
                        and not _loaded(rel, collected):
                    continue        # not on pytest's way to (or inside) what it collects
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
                    replay=f"rlgate check {task.origin or task.id} --only faked",
                ),
                fix_hint="overwrite all test-infra from a read-only reference before scoring; "
                         "reject (don't record) patches that touch files the gold never does.",
            ))
        return findings

    @staticmethod
    def _pytest_inis(root: Path, collected: set[str] | None) -> set[str]:
        """The ini file(s) pytest takes its settings from: from each place it collects,
        the nearest folder (going up) with one — the root when it collects everything."""
        starts = [""] if collected is None else sorted(collected)
        found = set()
        for c in starts:
            parts = c.split("/") if c else []
            if parts and not (root / c).is_dir():
                parts = parts[:-1]                   # a file: start from its folder
            for k in range(len(parts), -1, -1):
                folder = "/".join(parts[:k])
                hit = next((f"{folder}/{name}".lstrip("/") for name, section in _INI_ORDER
                            if (root / folder / name).is_file()
                            and (not section or _reads(root / folder / name, section))), None)
                if hit:
                    found.add(hit)
                    break
        return found

    def _candidates(self, root: Path):
        for p in root.rglob("*"):
            if any(part in _PRUNE for part in p.relative_to(root).parts):
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if p.name in _DANGER_NAMES:
                yield rel, p.name
            elif p.name.endswith("_test.go") and _reads(p, "func TestMain("):
                yield rel, "TestMain"


def _loaded(conftest: str, collected: set[str]) -> bool:
    """pytest loads a conftest in a folder above where it collects, or inside it."""
    folder = conftest.rsplit("/", 1)[0] + "/" if "/" in conftest else ""
    return any(c.startswith(folder) or folder.startswith(c.rstrip("/") + "/")
               for c in collected)
