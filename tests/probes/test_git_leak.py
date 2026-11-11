"""RG301 on real local repos: later history, an orphan task base over upstream refs, loose blobs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Task
from rlgate.probes.leakage.git_unreachable_objects import GitUnreachableObjects

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


class _Env:
    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def exec(self, cmd: str, timeout: int = 0):
        r = subprocess.run(["sh", "-c", cmd], cwd=self.root, capture_output=True, text=True,
                           timeout=60, check=False)
        return SimpleNamespace(stdout=r.stdout, stderr=r.stderr, exit_code=r.returncode)


def _git(root: Path, *args: str, date: str = "2024-01-01T00:00:00") -> str:
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "HOME": str(root),
           "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=root, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(root: Path, name: str, text: str, date: str) -> None:
    (root / name).write_text(text)
    _git(root, "add", name)
    _git(root, "commit", "-qm", text, date=date)


def _run(root: Path):
    task = Task(id="t", source="fixture", repo_dir=root, grader=GraderSpec(run="grade"))
    ctx = ProbeContext(sandbox=SimpleNamespace(agent_env=lambda: _Env(root)),
                       baseline=Baseline(1.0, 0.0, 1), variants=None)
    return GitUnreachableObjects().run(task, ctx)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "a.py", "v1", "2024-01-01T00:00:00")
    return tmp_path


def test_clean_repo_is_quiet(repo):
    assert _run(repo) == []


def test_a_branch_past_head_is_proven(repo):
    _commit(repo, "a.py", "the fix", "2024-02-01T00:00:00")
    _git(repo, "checkout", "-q", "HEAD~1")
    [f] = _run(repo)
    assert f.verdict is VerdictTier.CONFIRMED and "refs/heads/main holds 1 commit" in f.title


def test_an_orphan_task_base_over_upstream_refs_needs_judgment(repo):
    # the MiMo shape: a fresh "task base" commit with the upstream branch and tag kept around
    _commit(repo, "a.py", "upstream fix", "2026-07-20T00:00:00")
    _git(repo, "tag", "v2")
    _git(repo, "checkout", "-q", "--orphan", "task")
    _git(repo, "commit", "-qm", "ci | task base", date="2025-01-01T00:00:00")
    [f] = _run(repo)
    assert f.verdict is VerdictTier.REVIEW
    assert "2 ref(s)" in f.title and "newer than the starting commit" in f.title


def test_older_side_branch_says_none_newer(repo):
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "b.py", "old side", "2023-01-01T00:00:00")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "a.py", "v2", "2024-03-01T00:00:00")
    [f] = _run(repo)
    assert f.verdict is VerdictTier.REVIEW and "none newer" in f.title


def test_a_loose_unreachable_blob_needs_judgment(repo):
    subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="answer\n",
                   text=True, check=True, capture_output=True)
    [f] = _run(repo)
    assert f.verdict is VerdictTier.REVIEW and "1 blob" in f.title


def test_a_dropped_commit_is_proven(repo):
    _commit(repo, "a.py", "the fix", "2024-02-01T00:00:00")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    titles = [f.title for f in _run(repo) if f.verdict is VerdictTier.CONFIRMED]
    assert any("unreachable git commit" in t for t in titles)
