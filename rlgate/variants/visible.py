"""Which graded tests the agent can see.

A test is *visible* when its file is in the agent's workspace (it can read it, and learn its
inputs); every other graded test is *held out* (uploaded only at grading time, or baked into
a separate verifier image). The hardcode probe needs the split: answers recorded from the
visible tests must not pass the held-out ones. The test-rewrite probe needs the visible test
files: the agent can edit those.

Test ids come from the grader's per-test report (baseline.per_test). Their shape depends on
the format (``tests/test_x.py::TestA::test_b``, JUnit's ``tests.test_x.TestA.test_b``, an
absolute ``/app/tests/test_x.py::test_b``); ``test_file`` maps each back to a workspace file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rlgate.core.context import Baseline
from rlgate.core.task import Task
from rlgate.variants.python import is_test_file

_PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "site-packages",
          "dist", "build", ".mypy_cache", ".pytest_cache"}


@dataclass(frozen=True)
class Split:
    visible: list[str]          # graded test ids whose file is in the workspace
    hidden: list[str]           # graded test ids from anywhere else
    files: list[str]            # workspace files holding the visible graded tests
    known: bool                 # True: from a per-test report; False: inferred from layout


def held_out(task: Task) -> bool:
    """The grader brings tests the workspace doesn't have (uploaded or baked in)."""
    return len(task.test_roots) > 1


def test_file(test_id: str, repo: Path) -> str | None:
    """The workspace-relative file a test id comes from, or None if it isn't in it."""
    head = test_id.split("::", 1)[0]
    if head.endswith(".py") or "/" in head:
        parts = head.strip("/").split("/")
        for i in range(len(parts)):           # strip leading dirs (/app/…, the workdir)
            cand = "/".join(parts[i:])
            if cand and (repo / cand).is_file():
                return cand
        return None
    parts = test_id.split(".")
    for i in range(len(parts) - 1, 0, -1):    # JUnit dotted: module path, then class/name
        cand = "/".join(parts[:i]) + ".py"
        if (repo / cand).is_file():
            return cand
    return None


def workspace_tests(repo: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo)
        if any(part in _PRUNE for part in rel.parts):
            continue
        if is_test_file(rel.as_posix()):
            out.append(rel.as_posix())
    return out


def split(task: Task, baseline: Baseline) -> Split | None:
    """The visible / held-out split, or None when it can't be told (held-out tests exist
    but there is no per-test report to say which graded test is which)."""
    repo = Path(task.repo_dir)
    graded = sorted(t for t, ok in baseline.per_test.items() if ok)
    if graded:
        visible, hidden, files = [], [], set()
        for t in graded:
            f = test_file(t, repo)
            if f is None:
                hidden.append(t)
            else:
                visible.append(t)
                files.add(f)
        return Split(visible, hidden, sorted(files), known=True)
    files = workspace_tests(repo)
    if held_out(task):
        if not files:                       # the agent's workspace holds no test at all
            return Split([], [], [], known=True)
        return None
    # no report and no held-out tests: everything the grader runs is in the workspace
    return Split([], [], files, known=False)
