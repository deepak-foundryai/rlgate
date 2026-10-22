"""Docker sandbox: real isolation for running untrusted variants, container graders, and the
live forgery/race probes. Same ABC as the local backend.

Model: build the image once; keep a pristine host copy of the workspace as the snapshot.
Each overlay is a fresh container plus a host copy with the variant patch applied (zero fuzz,
shared logic) and copied in — so variant generation is identical to the local backend and the
image needs no git/patch tooling.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from rlgate.sandbox._patch import apply_zero_fuzz
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox


class DockerUnavailable(RuntimeError):
    pass


def unavailable_reason() -> str | None:
    """None if the Docker backend can be used, else a human-readable reason + fix."""
    try:
        ok = subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0
    except FileNotFoundError:
        return "docker is not installed — install Docker, or use --backend daytona/local"
    return None if ok else ("the Docker daemon is not running — start Docker, or use "
                            "--backend daytona/local")


def available() -> bool:
    return unavailable_reason() is None


def _docker(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False, **kw)


class DockerOverlay(Overlay):
    def __init__(self, container_id: str, host_copy: Path, sandbox: DockerSandbox) -> None:
        self.container_id = container_id
        self.root = host_copy          # host-side view (for parity); container path is /work
        self._sandbox = sandbox

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        p = _docker("exec", "-w", "/work", self.container_id, "sh", "-c", cmd, timeout=timeout)
        return ExecResult(p.returncode, p.stdout, p.stderr)

    def read_perms(self, path: str) -> Perms:
        p = _docker("exec", self.container_id, "stat", "-c", "%a", f"/work/{path.lstrip('/')}")
        if p.returncode != 0:
            return Perms(mode=0, exists=False)
        return Perms(mode=int(p.stdout.strip(), 8))

    def close(self) -> None:
        _docker("rm", "-f", self.container_id)
        self._sandbox._live.discard(self.container_id)
        shutil.rmtree(self.root, ignore_errors=True)


class DockerSandbox(Sandbox):
    def __init__(self, workspace: Path, image_ref: str | None = None,
                 dockerfile: Path | None = None, task_id: str = "task") -> None:
        self.workspace = Path(workspace)
        self.image_ref = image_ref
        self.dockerfile = Path(dockerfile) if dockerfile else None
        self.task_id = task_id
        self._tag: str | None = None
        self._snapshot: Path | None = None
        self._live: set[str] = set()

    @classmethod
    def from_task(cls, task) -> DockerSandbox:
        """grader.image is either a Dockerfile path (build it) or an existing image ref."""
        img = task.grader.image
        p = Path(img) if img else None
        if p and p.exists() and p.is_file():
            return cls(task.repo_dir, dockerfile=p, task_id=task.id)
        return cls(task.repo_dir, image_ref=img, task_id=task.id)

    def build(self) -> None:
        if not available():
            raise DockerUnavailable("docker is not available on this host")
        if self.dockerfile:
            self._tag = f"rlgate-{self.task_id}-{uuid.uuid4().hex[:8]}"
            r = _docker("build", "-t", self._tag, "-f", str(self.dockerfile),
                        str(self.dockerfile.parent))
            if r.returncode != 0:
                raise DockerUnavailable(f"image build failed:\n{r.stderr}")
        else:
            self._tag = self.image_ref
        # pristine host copy of the workspace (the snapshot we cp into each container)
        snap = Path(tempfile.mkdtemp(prefix="rlgate-dsnap-"))
        shutil.rmtree(snap)
        shutil.copytree(self.workspace, snap, symlinks=True)
        self._snapshot = snap

    def overlay(self, patch_text: str | None = None) -> DockerOverlay:
        assert self._snapshot is not None and self._tag is not None, "call build() first"
        host_copy = Path(tempfile.mkdtemp(prefix="rlgate-dovl-"))
        shutil.rmtree(host_copy)
        shutil.copytree(self._snapshot, host_copy, symlinks=True)
        if patch_text:
            apply_zero_fuzz(host_copy, patch_text)

        run = _docker("run", "-d", "--network=none", "-w", "/work",
                      self._tag, "sleep", "infinity")
        if run.returncode != 0:
            raise DockerUnavailable(f"container start failed:\n{run.stderr}")
        cid = run.stdout.strip()
        self._live.add(cid)
        _docker("exec", cid, "mkdir", "-p", "/work")
        cp = _docker("cp", f"{host_copy}/.", f"{cid}:/work")
        if cp.returncode != 0:
            _docker("rm", "-f", cid)
            raise DockerUnavailable(f"workspace copy failed:\n{cp.stderr}")
        return DockerOverlay(cid, host_copy, self)

    def pkill_user(self, user: str) -> None:
        """Kill detached candidate processes in every live container before scoring."""
        for cid in list(self._live):
            _docker("exec", cid, "pkill", "-u", user)
