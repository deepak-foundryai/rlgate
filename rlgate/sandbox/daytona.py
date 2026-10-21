"""Daytona sandbox: cloud-isolated execution via the Daytona SDK. This is the default
container backend (preferred over local Docker).

Model mirrors the Docker backend: build keeps a pristine host copy of the workspace; each
overlay applies the variant patch on the host (shared zero-fuzz logic), tars it, spins up a
fresh Daytona sandbox from the configured snapshot/image, uploads + extracts the tarball to
/work, and runs the grader there. One upload per overlay (a tarball), not per file.

Config comes from the environment (loaded from .env if present):
  DAYTONA_API_KEY, DAYTONA_API_URL, DAYTONA_SNAPSHOT (optional base snapshot).
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from rlgate.config import load_dotenv
from rlgate.sandbox._patch import apply_zero_fuzz
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox

WORK = "/work"
_REMOTE_TAR = "/tmp/rlgate_ws.tgz"


class DaytonaUnavailable(RuntimeError):
    pass


def available() -> bool:
    """Keys present and the SDK importable."""
    load_dotenv()
    if not os.environ.get("DAYTONA_API_KEY"):
        return False
    try:
        import daytona  # noqa: F401
    except ImportError:
        return False
    return True


class DaytonaOverlay(Overlay):
    def __init__(self, sandbox_handle, client, host_copy: Path, parent: DaytonaSandbox) -> None:
        self._sb = sandbox_handle
        self._client = client
        self.root = host_copy
        self._parent = parent

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        r = self._sb.process.exec(cmd, cwd=WORK, timeout=timeout)
        return ExecResult(exit_code=r.exit_code, stdout=r.result or "", stderr="")

    def read_perms(self, path: str) -> Perms:
        r = self._sb.process.exec(f"stat -c %a {WORK}/{path.lstrip('/')}", timeout=30)
        if r.exit_code != 0:
            return Perms(mode=0, exists=False)
        return Perms(mode=int((r.result or "0").strip(), 8))

    def close(self) -> None:
        try:
            self._client.delete(self._sb)
        finally:
            self._parent._live.discard(id(self._sb))
            shutil.rmtree(self.root, ignore_errors=True)


class DaytonaSandbox(Sandbox):
    def __init__(self, workspace: Path, snapshot: str | None = None,
                 image: str | None = None, task_id: str = "task") -> None:
        self.workspace = Path(workspace)
        self.snapshot = snapshot
        self.image = image
        self.task_id = task_id
        self._client = None
        self._host_snapshot: Path | None = None
        self._base_snapshot: str | None = None
        self._live: set[int] = set()
        self._handles: dict[int, object] = {}

    @classmethod
    def from_task(cls, task) -> DaytonaSandbox:
        """A local Dockerfile path can't be a Daytona image ref — fall back to the env
        snapshot in that case; a registry ref is used directly."""
        img = task.grader.image
        p = Path(img) if img else None
        image_ref = img if (img and not (p and p.exists())) else None
        return cls(task.repo_dir, image=image_ref, task_id=task.id)

    def build(self) -> None:
        if not available():
            raise DaytonaUnavailable("DAYTONA_API_KEY missing or daytona SDK not installed")
        from daytona import Daytona

        self._client = Daytona()  # reads DAYTONA_API_KEY / DAYTONA_API_URL / DAYTONA_TARGET
        self._base_snapshot = os.environ.get("DAYTONA_SNAPSHOT") or self.snapshot

        snap = Path(tempfile.mkdtemp(prefix="rlgate-dtsnap-"))
        shutil.rmtree(snap)
        shutil.copytree(self.workspace, snap, symlinks=True)
        self._host_snapshot = snap

    def overlay(self, patch_text: str | None = None) -> DaytonaOverlay:
        assert self._host_snapshot is not None and self._client is not None, "call build() first"
        host_copy = Path(tempfile.mkdtemp(prefix="rlgate-dtovl-"))
        shutil.rmtree(host_copy)
        shutil.copytree(self._host_snapshot, host_copy, symlinks=True)
        if patch_text:
            apply_zero_fuzz(host_copy, patch_text)

        tar_path = host_copy.with_suffix(".tgz")
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(host_copy, arcname=".")

        sb = self._create_sandbox()
        try:
            sb.fs.upload_file(str(tar_path), _REMOTE_TAR)
            res = sb.process.exec(f"mkdir -p {WORK} && tar xzf {_REMOTE_TAR} -C {WORK}", timeout=300)
            if res.exit_code != 0:
                raise DaytonaUnavailable(f"workspace extract failed: {res.result}")
        finally:
            tar_path.unlink(missing_ok=True)

        self._live.add(id(sb))
        self._handles[id(sb)] = sb
        return DaytonaOverlay(sb, self._client, host_copy, self)

    def _create_sandbox(self):
        from daytona import CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams

        if self._base_snapshot:
            return self._client.create(
                CreateSandboxFromSnapshotParams(snapshot=self._base_snapshot))
        image = self.image or "debian:12.9"
        return self._client.create(CreateSandboxFromImageParams(image=image))

    def pkill_user(self, user: str) -> None:
        for sid in list(self._live):
            sb = self._handles.get(sid)
            if sb is not None:
                sb.process.exec(f"pkill -u {user}", timeout=30)
