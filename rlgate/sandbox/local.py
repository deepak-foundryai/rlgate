"""Local sandbox: copy the workspace to a temp dir, apply a patch with zero fuzz, run the
grader via subprocess. No isolation guarantees — for dev and CI fixtures only. The Docker
backend implements the same ABC for real isolation (and the race/perms probes)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from rlgate.sandbox._patch import PatchConflict, apply_zero_fuzz
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox
from rlgate.sandbox.errors import IsolationUnavailable

__all__ = ["LocalOverlay", "LocalSandbox", "PatchConflict"]


class LocalOverlay(Overlay):
    def __init__(self, root: Path, inject: tuple[tuple[str, str], ...] = ()) -> None:
        self.root = root
        self._inject = inject

    def before_grade(self) -> None:
        """Held-out tests arrive only now, after the agent's work: each (host dir, dest) is
        copied to ``dest`` inside the overlay (replacing whatever the agent left there)."""
        for host, dest in self._inject:
            target = self.root / dest.lstrip("/")
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.copytree(host, target, symlinks=True)

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        p = subprocess.run(
            cmd, shell=True, cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
            env={**os.environ, "RLGATE_SANDBOX": "local"},
        )
        return ExecResult(p.returncode, p.stdout, p.stderr)

    def read_perms(self, path: str) -> Perms:
        target = self.root / path.lstrip("/")
        if not target.exists():
            return Perms(mode=0, exists=False)
        return Perms(mode=os.stat(target).st_mode & 0o777)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class LocalSandbox(Sandbox):
    def __init__(self, workspace: Path, inject: tuple[tuple[str, str], ...] = ()) -> None:
        self.workspace = Path(workspace)
        self.inject = inject                  # held-out test dirs, copied in before grading
        self._snapshot: Path | None = None

    def build(self) -> None:
        snap = Path(tempfile.mkdtemp(prefix="rlgate-snap-"))
        shutil.rmtree(snap)
        shutil.copytree(self.workspace, snap, symlinks=True)
        self._snapshot = snap

    def agent_env(self):
        raise NotImplementedError("the local backend has no agent environment to inspect")

    def overlay(self, patch_text: str | None = None, *, isolated: bool = False) -> LocalOverlay:
        # NOTE: a local overlay is a temp dir on this host — never isolated from the host
        # itself. Probes whose payloads write outside the workspace must not run here.
        assert self._snapshot is not None, "call build() first"
        if isolated:
            raise IsolationUnavailable(
                "this check writes outside the workspace; it needs --backend docker or daytona")
        root = Path(tempfile.mkdtemp(prefix="rlgate-ovl-"))
        shutil.rmtree(root)
        shutil.copytree(self._snapshot, root, symlinks=True)
        if patch_text:
            apply_zero_fuzz(root, patch_text)
        return LocalOverlay(root, self.inject)

    def close(self) -> None:
        if self._snapshot is not None:
            shutil.rmtree(self._snapshot, ignore_errors=True)
            self._snapshot = None

    def pkill_user(self, user: str) -> None:
        subprocess.run(["pkill", "-u", user], capture_output=True, check=False)
