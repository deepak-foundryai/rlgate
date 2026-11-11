"""Machines: the smallest surface a container environment needs to offer — run a command,
put a file, get a file — so the staged backend (``sandbox.staged``) works the same on a
Docker container, a Daytona sandbox, or one service of a compose stack running in either.

Directory transfer is built on top, once, as tarballs: ``upload_dir`` / ``download_dir``.
Extraction on our side uses tarfile's ``data`` filter (no absolute paths, no escaping
symlinks, no device files), because what comes out of a task image is untrusted.
"""

from __future__ import annotations

import filecmp
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from rlgate.sandbox.base import ExecResult
from rlgate.sandbox.errors import BackendUnavailable

# Dependency/cache trees never worth pulling out of an image (unless an artifact IS one).
HEAVY_DIRS = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                        ".mypy_cache", ".ruff_cache", ".next", ".cache", ".tox", ".gradle"})


class MachineError(BackendUnavailable):
    """An environment command (build, copy, start) failed."""


class Machine(ABC):
    """One running container (or the host). Paths are absolute, inside the machine."""

    @abstractmethod
    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult: ...

    @abstractmethod
    def put_file(self, local: Path, remote: str) -> None: ...

    @abstractmethod
    def get_file(self, remote: str, local: Path) -> None: ...

    def close(self) -> None:
        """Release it (delete the container / sandbox). Idempotent."""

    def check(self, cmd: str, *, what: str, timeout: int = 900, cwd: str | None = None) -> str:
        r = self.exec(cmd, timeout=timeout, cwd=cwd)
        if r.exit_code != 0:
            out = (r.stdout + r.stderr).strip()[-2000:]
            raise MachineError(f"{what} failed (exit {r.exit_code}):\n{out}")
        return r.stdout


# -- transfer helpers -------------------------------------------------------------------


def upload_dir(m: Machine, local: Path, remote: str, *, replace: bool = False) -> None:
    """Copy the CONTENTS of ``local`` into ``remote`` (created if missing). ``replace``
    empties ``remote`` first, so files the candidate deleted are gone there too."""
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-up-"))
    try:
        tgz = tmp / "d.tgz"
        with tarfile.open(tgz, "w:gz") as tf:
            for child in sorted(local.iterdir()) if local.is_dir() else []:
                tf.add(child, arcname=child.name)
        dest = f"/tmp/rlgate-{uuid.uuid4().hex[:12]}.tgz"
        m.put_file(tgz, dest)
        q = shlex.quote(remote)
        wipe = f"rm -rf {q} && " if replace else ""
        m.check(f"{wipe}mkdir -p {q} && tar xzf {dest} -C {q} --no-same-owner "
                f"&& rm -f {dest}", what=f"upload to {remote}", timeout=600)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def upload_file(m: Machine, local: Path, remote: str) -> None:
    parent = os.path.dirname(remote) or "/"
    m.check(f"mkdir -p {shlex.quote(parent)}", what=f"mkdir {parent}", timeout=60)
    m.put_file(local, remote)


def download_dir(m: Machine, remote: str, local: Path,
                 exclude: Iterable[str] = ()) -> None:
    """Copy the contents of ``remote`` into ``local`` (created). ``exclude`` names
    directories skipped at any depth (e.g. ``node_modules``)."""
    q = shlex.quote(remote)
    src = f"/tmp/rlgate-{uuid.uuid4().hex[:12]}.tgz"
    excl = " ".join(f"--exclude={shlex.quote(e)}" for e in sorted(exclude))
    m.check(f"tar czf {src} {excl} -C {q} .", what=f"archive {remote}", timeout=900)
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-down-"))
    try:
        tgz = tmp / "d.tgz"
        m.get_file(src, tgz)
        m.exec(f"rm -f {src}", timeout=60)
        local.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(local, filter=_keep_links)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _keep_links(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    """The safe ``data`` filter, except that a symlink pointing outside the tree (a venv's
    ``python -> /usr/bin/python3``) is kept as it is: real images are full of them, and the
    copy must match the container. Nothing is written through such a link: every later
    member is still checked against the real destination path."""
    try:
        return tarfile.data_filter(member, dest)
    except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
        if member.issym():
            return tarfile.tar_filter(member, dest)
        raise


def remote_kind(m: Machine, path: str) -> str | None:
    """"dir" | "file" | None (missing) for a path inside the machine."""
    q = shlex.quote(path)
    r = m.exec(f"if [ -d {q} ]; then echo dir; elif [ -e {q} ]; then echo file; fi",
               timeout=60)
    out = r.stdout.strip()
    return out if out in ("dir", "file") else None


def changed_files(base: Path, new: Path) -> tuple[list[str], list[str]]:
    """(written, deleted) relative paths that differ between two trees. Symlinks compare by
    target; regular files by content (size/mtime short-circuit first)."""
    def walk(root: Path) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            for name in filenames + [n for n in dirnames if (d / n).is_symlink()]:
                p = d / name
                out[p.relative_to(root).as_posix()] = p
        return out

    a, b = walk(base), walk(new)
    written: list[str] = []
    for rel, p in b.items():
        q = a.get(rel)
        if q is None:
            written.append(rel)
        elif p.is_symlink() or q.is_symlink():
            if not (p.is_symlink() and q.is_symlink() and os.readlink(p) == os.readlink(q)):
                written.append(rel)
        elif not filecmp.cmp(p, q, shallow=False) or \
                (os.stat(p).st_mode & 0o777) != (os.stat(q).st_mode & 0o777):
            written.append(rel)
    deleted = [rel for rel in a if rel not in b]
    return sorted(written), sorted(deleted)


def push_delta(m: Machine, new: Path, remote_root: str, written: list[str],
               deleted: list[str]) -> None:
    """Make ``remote_root`` match ``new`` on the given paths only — untouched files keep
    the image's own ownership and metadata."""
    root = shlex.quote(remote_root)
    if deleted:
        args = " ".join(shlex.quote(d) for d in deleted)
        m.check(f"cd {root} && rm -rf -- {args}", what="delete files", timeout=120)
    if not written:
        return
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-delta-"))
    try:
        tgz = tmp / "d.tgz"
        with tarfile.open(tgz, "w:gz") as tf:
            for rel in written:
                tf.add(new / rel, arcname=rel, recursive=False)
        dest = f"/tmp/rlgate-{uuid.uuid4().hex[:12]}.tgz"
        m.put_file(tgz, dest)
        m.check(f"mkdir -p {root} && tar xzf {dest} -C {root} --no-same-owner "
                f"&& rm -f {dest}", what="apply changes", timeout=600)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -- implementations ---------------------------------------------------------------------


class HostMachine(Machine):
    """This machine. Used as the compose host for a local Docker daemon."""

    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult:
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired as e:
            return ExecResult(124, _s(e.stdout), _s(e.stderr) + f"\n[timed out after {timeout}s]")
        return ExecResult(p.returncode, p.stdout, p.stderr)

    def put_file(self, local: Path, remote: str) -> None:
        Path(remote).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, remote)

    def get_file(self, remote: str, local: Path) -> None:
        shutil.copyfile(remote, local)


class DockerMachine(Machine):
    """One container on a Docker daemon reachable from ``host`` (this machine by default)."""

    def __init__(self, container: str, host: Machine | None = None,
                 workdir: str | None = None) -> None:
        self.container = container
        self.host = host or HostMachine()
        self.workdir = workdir

    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult:
        wd = cwd or self.workdir
        w = f"-w {shlex.quote(wd)} " if wd else ""
        return self.host.exec(f"docker exec {w}{self.container} sh -c {shlex.quote(cmd)}",
                              timeout=timeout)

    def put_file(self, local: Path, remote: str) -> None:
        self.host.check(f"docker cp {shlex.quote(str(local))} "
                        f"{self.container}:{shlex.quote(remote)}", what="docker cp in")

    def get_file(self, remote: str, local: Path) -> None:
        self.host.check(f"docker cp {self.container}:{shlex.quote(remote)} "
                        f"{shlex.quote(str(local))}", what="docker cp out")

    def close(self) -> None:
        self.host.exec(f"docker rm -f {self.container}", timeout=120)


class ComposeMachine(Machine):
    """One service of a compose project running on ``host`` (the Docker host: this machine
    or a Docker-in-Docker sandbox). Files go host → service with ``docker compose cp``."""

    def __init__(self, host: Machine, compose_cmd: str, service: str,
                 workdir: str | None = None, on_close=None) -> None:
        self.host = host
        self.compose = compose_cmd          # e.g. "docker compose -p rg1 -f /x/c.yaml"
        self.service = service
        self.workdir = workdir
        self._on_close = on_close

    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult:
        wd = cwd or self.workdir
        w = f"-w {shlex.quote(wd)} " if wd else ""
        return self.host.exec(f"{self.compose} exec -T {w}{self.service} "
                              f"sh -c {shlex.quote(cmd)}", timeout=timeout)

    def put_file(self, local: Path, remote: str) -> None:
        stage = f"/tmp/rlgate-{uuid.uuid4().hex[:12]}"
        self.host.put_file(local, stage)
        try:
            self.host.check(f"{self.compose} cp {stage} {self.service}:{shlex.quote(remote)} "
                            f"&& rm -f {stage}", what="compose cp in")
        except MachineError as e:
            raise MachineError(f"{e}{self._stopped()}") from e

    def get_file(self, remote: str, local: Path) -> None:
        stage = f"/tmp/rlgate-{uuid.uuid4().hex[:12]}"
        self.host.check(f"{self.compose} cp {self.service}:{shlex.quote(remote)} {stage}",
                        what="compose cp out")
        try:
            self.host.get_file(stage, local)
        finally:
            self.host.exec(f"rm -f {stage}", timeout=60)

    def _stopped(self) -> str:
        """Why the copy failed when the service is not running (Docker says "Could not
        find the file" for a stopped container): its state, exit code and OOM flag."""
        r = self.host.exec(f"docker inspect -f '{{{{.State.Status}}}} "
                           f"exit={{{{.State.ExitCode}}}} oom={{{{.State.OOMKilled}}}}' "
                           f"$({self.compose} ps -aq {self.service})", timeout=120)
        state = r.stdout.strip()
        if r.exit_code != 0 or not state or state.startswith("running"):
            return ""
        hint = (" — it ran out of memory; give the sandbox more (--backend-opt memory_gb=N)"
                if "oom=true" in state else "")
        return f"\nthe {self.service!r} container is not running ({state}){hint}"

    def close(self) -> None:
        if self._on_close is not None:
            cb, self._on_close = self._on_close, None
            cb()


def _s(v) -> str:
    if v is None:
        return ""
    return v.decode(errors="replace") if isinstance(v, bytes) else str(v)
