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

__all__ = ["LocalOverlay", "LocalSandbox", "PatchConflict"]


class LocalOverlay(Overlay):
    def __init__(self, root: Path) -> None:
        self.root = root

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        p = subprocess.run(
            cmd, shell=True, cwd=self.root, capture_output=True, text=True,
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
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._snapshot: Path | None = None

    def build(self) -> None:
        snap = Path(tempfile.mkdtemp(prefix="rlgate-snap-"))
        shutil.rmtree(snap)
        shutil.copytree(self.workspace, snap, symlinks=True)
        self._snapshot = snap

    def overlay(self, patch_text: str | None = None) -> LocalOverlay:
        assert self._snapshot is not None, "call build() first"
        root = Path(tempfile.mkdtemp(prefix="rlgate-ovl-"))
        shutil.rmtree(root)
        shutil.copytree(self._snapshot, root, symlinks=True)
        if patch_text:
            apply_zero_fuzz(root, patch_text)
        return LocalOverlay(root)

    def pkill_user(self, user: str) -> None:
        subprocess.run(["pkill", "-u", user], capture_output=True, check=False)
