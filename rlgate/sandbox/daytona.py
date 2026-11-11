"""Daytona sandbox: cloud-isolated grading via the Daytona SDK.

What runs (in order of precedence; the same image rules as the Docker backend):
  1. an explicit snapshot (``--backend-opt snapshot=NAME`` / ``[environment] snapshot``);
  2. an explicit registry image (``image=`` kwarg);
  3. the task's own Dockerfile (``grader.image`` is a file or a dir holding one — Harbor's
     ``environment/Dockerfile``), built by Daytona via ``Image.from_dockerfile`` and cached as
     a snapshot named by the content hash of the build context + resources
     (``rlgate-<hash>``), so it is built once per distinct environment, never per grade;
  4. the task's registry ref (``grader.image`` that isn't a local path);
  5. for tasks that declare no image: ``DAYTONA_SNAPSHOT`` if set, else ``default_image``
     (debian:12.9). A global env var never overrides a task's own environment.

How it runs: ONE sandbox per task, created on the first grade and reused. Each overlay
re-extracts a fresh workspace (patch applied on the host, uploaded as one tarball), so
grades never see each other's workspace. ``overlay(isolated=True)`` gets a dedicated
sandbox for payloads that write outside the workspace. Every sandbox carries an auto-stop
interval, immediate auto-delete once stopped, a hard TTL and ``rlgate`` labels, so a crashed
or killed audit can't leave machines running; ``close()`` (and an atexit hook) delete them
right away in the normal case.

Credentials come from the environment (or ``.env``): ``DAYTONA_API_KEY`` and optionally
``DAYTONA_API_URL`` / ``DAYTONA_TARGET``. Tunables come from ``rlgate.toml``
``[environment]`` or ``--backend-opt key=value`` — see ``DaytonaOptions``.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from rlgate import ui
from rlgate.config import load_dotenv
from rlgate.sandbox._image import context_hash, resolve_dockerfile
from rlgate.sandbox._patch import apply_zero_fuzz
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox
from rlgate.sandbox.errors import BackendUnavailable
from rlgate.sandbox.machine import Machine, MachineError

log = logging.getLogger(__name__)

_REMOTE_TAR = "/tmp/rlgate_ws.tgz"


class DaytonaUnavailable(BackendUnavailable):
    pass


@dataclass(frozen=True)
class DaytonaOptions:
    """Every knob, with safe defaults. Unknown keys are rejected (typos fail loudly)."""

    cpus: int | None = None               # vCPUs (None = Daytona's default)
    memory_gb: int | None = None
    disk_gb: int | None = None
    workdir: str = "/work"                # where the workspace is extracted / grader runs
    snapshot: str | None = None           # use this existing snapshot as-is
    image: str | None = None              # override the task's image with a registry ref
    default_image: str = "debian:12.9"    # when the task declares no image at all
    cache_snapshots: bool = True          # build Dockerfiles once into rlgate-<hash>
    reuse_sandbox: bool = True            # one sandbox per task (False = one per grade)
    auto_stop_minutes: int = 30           # idle → stopped → deleted (crash safety)
    ttl_minutes: int = 240                # hard wall-clock cap per sandbox (crash safety)
    network_block_all: bool = False
    network_allow_list: str | None = None  # comma-separated CIDRs, e.g. "10.0.0.0/8"
    create_timeout_s: float = 300
    build_timeout_s: float = 1800
    create_retries: int = 3               # transient API errors (429/5xx/network)
    # account limits: read from Daytona at run time; these only override or bound that
    max_snapshots: int | None = None      # snapshot cap when this API key can't read it
    snapshot_idle_minutes: float = 10     # rlgate-* snapshots unused this long may be freed
    snapshot_wait_s: float = 1800         # wait this long for a slot, then build per sandbox
    room_wait_s: float = 600              # wait this long for CPU/memory/disk room
    region: str | None = None             # region whose quota to watch (default: largest)
    dind_image: str = "docker:28.3.3-dind"   # compose stacks run in Docker-in-Docker
    dockerd_wait_s: float = 120

    @classmethod
    def from_kwargs(cls, kwargs: dict | None) -> DaytonaOptions:
        kwargs = dict(kwargs or {})
        valid = {f.name for f in fields(cls)}
        unknown = sorted(set(kwargs) - valid)
        if unknown:
            raise ValueError(f"unknown daytona option(s) {unknown}; valid: {sorted(valid)}")
        return cls(**kwargs)

    def resources(self):
        from daytona import Resources
        if self.cpus is None and self.memory_gb is None and self.disk_gb is None:
            return None
        return Resources(cpu=self.cpus, memory=self.memory_gb, disk=self.disk_gb)

    def resource_key(self) -> str:
        return f"cpu={self.cpus};mem={self.memory_gb};disk={self.disk_gb}"


def unavailable_reason() -> str | None:
    """None if Daytona can be used, else a human-readable reason + fix."""
    load_dotenv()
    if not os.environ.get("DAYTONA_API_KEY"):
        return ("DAYTONA_API_KEY is not set — export it, or put it in ./.env or "
                "~/.config/rlgate/.env; or use --backend docker/local")
    try:
        import daytona  # noqa: F401
    except ImportError:
        return "the daytona SDK is not installed — run: pip install 'rlgate[daytona]'"
    return None


def available() -> bool:
    return unavailable_reason() is None


# -- crash safety: delete every live sandbox on interpreter exit -----------------------
# A STRONG reference while remote machines exist: a weak one lets an un-closed
# DaytonaSandbox be garbage-collected and its remote sandbox orphaned until auto-stop.

_OPEN: set[DaytonaSandbox] = set()
_OPEN_GUARD = threading.Lock()


@atexit.register
def _close_all() -> None:  # pragma: no cover - exercised at interpreter exit
    with _OPEN_GUARD:
        pending = list(_OPEN)
    for sb in pending:
        try:
            sb.close()
        except Exception as e:  # noqa: BLE001 - best effort; auto-stop/TTL are the backstop
            log.warning("daytona cleanup at exit failed: %s", e)


# -- snapshot build coordination (one build per name per process) ----------------------

_SNAP_LOCKS: dict[str, threading.Lock] = {}
_SNAP_LOCKS_GUARD = threading.Lock()


def _snap_lock(name: str) -> threading.Lock:
    with _SNAP_LOCKS_GUARD:
        return _SNAP_LOCKS.setdefault(name, threading.Lock())


def _is_transient(e: Exception) -> bool:
    from daytona.common import errors as de
    return isinstance(e, (de.DaytonaRateLimitError, de.DaytonaConnectionError,
                          de.DaytonaServiceUnavailableError, de.DaytonaInternalServerError,
                          de.DaytonaBadGatewayError, de.DaytonaTimeoutError))


class _Refused(Exception):
    """Daytona would not build a snapshot, and not because the image is broken."""

    def __init__(self, msg: str, *, forbidden: bool = False) -> None:
        super().__init__(msg)
        self.forbidden = forbidden


class DaytonaOverlay(Overlay):
    def __init__(self, handle, host_copy: Path, parent: DaytonaSandbox, owned: bool) -> None:
        self._sb = handle
        self.root = host_copy
        self._parent = parent
        self._owned = owned            # True: a dedicated sandbox this overlay deletes

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        # Daytona returns stdout and stderr combined in `result`.
        r = self._sb.process.exec(cmd, cwd=self._parent.opts.workdir, timeout=timeout)
        return ExecResult(exit_code=r.exit_code, stdout=r.result or "", stderr="")

    def read_perms(self, path: str) -> Perms:
        target = f"{self._parent.opts.workdir}/{path.lstrip('/')}"
        r = self._sb.process.exec(f"stat -c %a {target}", timeout=30)
        if r.exit_code != 0:
            return Perms(mode=0, exists=False)
        return Perms(mode=int((r.result or "0").strip(), 8))

    def close(self) -> None:
        try:
            if self._owned:
                self._parent._delete(self._sb)
            else:
                self._parent._release_shared()
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


class DaytonaMachine(Machine):
    """A dedicated Daytona sandbox (deleted on ``close``) as a ``Machine``."""

    def __init__(self, handle, fleet: DaytonaSandbox, workdir: str | None = None) -> None:
        self.handle = handle
        self._fleet = fleet
        self.workdir = workdir
        self._closed = False

    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult:
        r = self.handle.process.exec(cmd, cwd=cwd or self.workdir, timeout=timeout)
        return ExecResult(exit_code=r.exit_code, stdout=r.result or "", stderr="")

    def put_file(self, local: Path, remote: str) -> None:
        self.handle.fs.upload_file(str(local), remote)

    def get_file(self, remote: str, local: Path) -> None:
        self.handle.fs.download_file(remote, str(local))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._fleet._delete(self.handle)


class DaytonaSandbox(Sandbox):
    def __init__(self, workspace: Path | None, *, dockerfile: Path | None = None,
                 image_ref: str | None = None, task_id: str = "task",
                 options: DaytonaOptions | None = None,
                 client_factory: Callable[[], Any] | None = None) -> None:
        self.workspace = Path(workspace) if workspace is not None else None
        self.dockerfile = dockerfile
        self.image_ref = image_ref
        self.task_id = task_id
        self.opts = options or DaytonaOptions()
        self.dind = False                  # sandboxes run dockerd (compose host)
        self._client_factory = client_factory
        self._client: Any = None
        self._source: tuple[str, Any] | None = None   # ("snapshot", name) | ("image", img)
        self._host_snapshot: Path | None = None
        self._shared: Any = None
        self._shared_busy = False
        self._live: dict[str, Any] = {}               # sandbox id → handle
        self._snapshot_origin = "configured"
        self._budget: Any = None
        self._held_snapshot: str | None = None   # released to the budget on close()
        self._lock = threading.Lock()

    @classmethod
    def from_task(cls, task, options: dict | DaytonaOptions | None = None) -> DaytonaSandbox:
        opts = options if isinstance(options, DaytonaOptions) else \
            DaytonaOptions.from_kwargs(options)
        img = task.grader.image
        dockerfile = resolve_dockerfile(img)
        return cls(task.repo_dir, dockerfile=dockerfile,
                   image_ref=None if dockerfile else img, task_id=task.id, options=opts)

    @classmethod
    def for_env(cls, env, task_id: str,
                options: dict | DaytonaOptions | None = None) -> DaytonaSandbox:
        """A machine factory (no workspace) for one ``EnvSpec``: its image or Dockerfile, or
        — for a compose stack — a Docker-in-Docker VM that runs the stack. The task's own
        resources apply unless set explicitly; ``snapshot``/``image`` overrides don't (they
        would replace an environment the task defines precisely)."""
        import dataclasses
        import math

        opts = options if isinstance(options, DaytonaOptions) else \
            DaytonaOptions.from_kwargs(options)
        if opts.snapshot or opts.image:
            log.warning("snapshot=/image= are ignored for tasks that define their own "
                        "environments (%s)", task_id)
        opts = dataclasses.replace(
            opts, snapshot=None, image=None,
            cpus=opts.cpus or (math.ceil(env.cpus) if env.cpus else None),
            memory_gb=opts.memory_gb or (math.ceil(env.memory_mb / 1024)
                                         if env.memory_mb else None),
            disk_gb=opts.disk_gb or (math.ceil(env.storage_mb / 1024)
                                     if env.storage_mb else None),
            build_timeout_s=max(opts.build_timeout_s, env.build_timeout_s),
        )
        if env.compose is not None:
            sb = cls(None, image_ref=opts.dind_image, task_id=task_id,
                     options=dataclasses.replace(opts, network_block_all=False))
            sb.dind = True
            return sb
        if env.dockerfile is not None:
            return cls(None, dockerfile=env.dockerfile, task_id=task_id, options=opts)
        return cls(None, image_ref=env.image, task_id=task_id, options=opts)

    def machine(self, workdir: str | None = None) -> DaytonaMachine:
        """A dedicated, fresh sandbox (call ``build()`` first). Docker-in-Docker sandboxes
        come back with dockerd running."""
        assert self._client is not None, "call build() first"
        handle = self._create()
        m = DaytonaMachine(handle, self, workdir)
        if self.dind:
            try:
                self._start_dockerd(m)
            except BaseException:
                m.close()
                raise
        return m

    def _start_dockerd(self, m: DaytonaMachine) -> None:
        # the dind image's entrypoint doesn't run under Daytona: start the daemon ourselves
        m.exec("nohup dockerd-entrypoint.sh dockerd > /var/log/dockerd.log 2>&1 &",
               timeout=15)
        deadline = time.monotonic() + self.opts.dockerd_wait_s
        while time.monotonic() < deadline:
            if m.exec("docker info > /dev/null 2>&1", timeout=15).exit_code == 0:
                return
            time.sleep(2)
        tail = m.exec("tail -n 40 /var/log/dockerd.log", timeout=15).stdout
        raise MachineError(f"Docker daemon did not start in the Daytona sandbox within "
                           f"{self.opts.dockerd_wait_s:g}s:\n{tail}")

    # -- lifecycle ------------------------------------------------------------------

    def build(self) -> None:
        if self._client_factory is None:
            reason = unavailable_reason()
            if reason:
                raise DaytonaUnavailable(reason)
            from daytona import Daytona
            self._client_factory = Daytona   # reads DAYTONA_API_KEY / _API_URL / _TARGET
        self._client = self._client_factory()
        from rlgate.sandbox.daytona_quota import budget_for
        self._budget = budget_for(self._client, max_snapshots=self.opts.max_snapshots,
                                  idle_minutes=self.opts.snapshot_idle_minutes,
                                  region=self.opts.region)
        self._source = self._resolve_source()
        if self._source[0] == "snapshot" and self._snapshot_state(self._source[1]) is None:
            origin = getattr(self, "_snapshot_origin", "configured")
            raise DaytonaUnavailable(
                f"Daytona snapshot {self._source[1]!r} ({origin}) does not exist in this "
                "Daytona organization — fix the name, create it, or remove the setting")

        if self.workspace is None:          # a machine factory (staged backend)
            return
        snap = Path(tempfile.mkdtemp(prefix="rlgate-dtsnap-"))
        shutil.rmtree(snap)
        shutil.copytree(self.workspace, snap, symlinks=True)
        self._host_snapshot = snap

    def close(self) -> None:
        with self._lock:
            handles = list(self._live.values())
            self._live.clear()
            self._shared = None
            self._shared_busy = False
        with _OPEN_GUARD:
            _OPEN.discard(self)
        for h in handles:
            self._delete(h, tracked=False)
        if self._held_snapshot is not None and self._budget is not None:
            self._budget.done(self._held_snapshot)
            self._held_snapshot = None
        if self._host_snapshot is not None:
            shutil.rmtree(self._host_snapshot, ignore_errors=True)
            self._host_snapshot = None

    def overlay(self, patch_text: str | None = None, *,
                isolated: bool = False) -> DaytonaOverlay:
        assert self._host_snapshot is not None and self._client is not None, \
            "call build() first"
        host_copy = Path(tempfile.mkdtemp(prefix="rlgate-dtovl-"))
        shutil.rmtree(host_copy)
        shutil.copytree(self._host_snapshot, host_copy, symlinks=True)
        try:
            if patch_text:
                apply_zero_fuzz(host_copy, patch_text)
            handle, owned = self._acquire(isolated)
        except BaseException:
            shutil.rmtree(host_copy, ignore_errors=True)
            raise
        try:
            self._load_workspace(handle, host_copy)
        except BaseException:
            if owned:
                self._delete(handle)
            else:
                self._release_shared()
            shutil.rmtree(host_copy, ignore_errors=True)
            raise
        return DaytonaOverlay(handle, host_copy, self, owned)

    def pkill_user(self, user: str) -> None:
        for h in list(self._live.values()):
            h.process.exec(f"pkill -u {user}", timeout=30)

    # -- image / snapshot -----------------------------------------------------------

    def _resolve_source(self) -> tuple[str, Any]:
        o = self.opts
        if o.snapshot:
            self._snapshot_origin = "from --backend-opt/rlgate.toml snapshot="
            return ("snapshot", o.snapshot)
        if o.image:
            return ("image", o.image)
        if self.dockerfile is not None:
            from daytona import Image
            image = Image.from_dockerfile(self.dockerfile)
            if o.cache_snapshots:
                name = self._ensure_snapshot(image)
                if name:
                    return ("snapshot", name)
            return ("image", image)
        if self.image_ref:
            return ("image", self.image_ref)
        load_dotenv()
        if os.environ.get("DAYTONA_SNAPSHOT"):
            self._snapshot_origin = "from DAYTONA_SNAPSHOT"
            return ("snapshot", os.environ["DAYTONA_SNAPSHOT"])
        return ("image", o.default_image)

    def _ensure_snapshot(self, image) -> str | None:
        """Get-or-build ``rlgate-<hash>`` within the account's snapshot limit (see
        daytona_quota). Returns None (→ build the image per sandbox) when no slot can be had
        or this key can't manage snapshots: a limit degrades the run, it never fails it."""
        assert self.dockerfile is not None
        name = f"rlgate-{context_hash(self.dockerfile, self.opts.resource_key())[:16]}"
        with _snap_lock(name):
            got = self._get_snapshot(name, image)
        if got:
            self._budget.use(got)
            self._held_snapshot = got
        return got

    def _get_snapshot(self, name: str, image) -> str | None:
        from daytona.common import errors as de

        assert self.dockerfile is not None
        state = self._snapshot_state(name)
        if state == "active":
            return name
        if state == "inactive":          # archived after disuse → wake it up
            try:
                self._client.snapshot.activate(name)
            except de.DaytonaError as e:
                log.warning("could not activate snapshot %s: %s", name, e)
                return None
            return name if self._wait_snapshot(name) else None
        if state in ("error", "build_failed"):
            log.warning("snapshot %s is in state %s; rebuilding", name, state)
            try:
                self._client.snapshot.delete(name)
            except de.DaytonaError:
                pass
        elif state is not None:          # building / pending / pulling elsewhere
            return name if self._wait_snapshot(name) else None

        if not self._budget.reserve(name, self.opts.snapshot_wait_s):
            log.warning("no Daytona snapshot slot free for %s; building the image per "
                        "sandbox instead", self.task_id)
            ui.progress("no Daytona snapshot slot free; building the image per sandbox")
            return None
        try:
            for attempt in (1, 2):
                try:
                    return self._build_snapshot(name, image)
                except _Refused as e:
                    # refused without the image failing: the account is full (the limit was
                    # unknown or changed meanwhile) or snapshots aren't allowed for this key
                    if attempt == 1 and not e.forbidden and self._budget.make_room():
                        continue
                    log.warning("Daytona refused snapshot %s (%s); building the image per "
                                "sandbox instead", name, e)
                    return None
            return None
        finally:
            self._budget.built(name)

    def _build_snapshot(self, name: str, image) -> str | None:
        from daytona import CreateSnapshotParams
        from daytona.common import errors as de

        log.info("building snapshot %s from %s (once per environment)", name, self.dockerfile)
        tail: deque[str] = deque(maxlen=40)
        ui.progress(f"Daytona is building snapshot {name} from the image (first run "
                    "only; large images take minutes)")

        def on_log(line: str) -> None:
            tail.append(line.rstrip())
            log.debug("[snapshot %s] %s", name, line)
            if line.strip():
                ui.progress(f"snapshot {name}: {line.strip()[:120]}")
        try:
            self._client.snapshot.create(
                CreateSnapshotParams(name=name, image=image, resources=self.opts.resources()),
                on_logs=on_log,
                timeout=self.opts.build_timeout_s,
            )
            return name
        except de.DaytonaConflictError:
            return name if self._wait_snapshot(name) else None   # another process won
        except (de.DaytonaForbiddenError, de.DaytonaAuthenticationError) as e:
            raise _Refused(str(e), forbidden=True) from e
        except de.DaytonaError as e:
            state = self._snapshot_state(name)
            # the image itself failed to build: a task defect (RG005), not infrastructure
            if state in ("error", "build_failed") and tail:
                from rlgate.sandbox.errors import BuildFailed
                raise BuildFailed([(f"image built from {self.dockerfile}",
                                    "\n".join(tail))]) from e
            if state in ("error", "build_failed"):
                try:
                    self._client.snapshot.delete(name)
                except de.DaytonaError:
                    pass
            if _is_transient(e) and state is None:
                raise
            raise _Refused(f"{type(e).__name__}: {e}") from e

    def _snapshot_state(self, name: str) -> str | None:
        from daytona.common import errors as de
        try:
            snap = self._client.snapshot.get(name)
        except de.DaytonaNotFoundError:
            return None
        state = getattr(snap, "state", None)
        return str(getattr(state, "value", state)).lower() if state is not None else None

    def _wait_snapshot(self, name: str) -> bool:
        deadline = time.monotonic() + self.opts.build_timeout_s
        while time.monotonic() < deadline:
            state = self._snapshot_state(name)
            ui.progress(f"waiting for Daytona snapshot {name} ({state or 'missing'})")
            if state == "active":
                return True
            if state in (None, "error", "build_failed"):
                return False
            time.sleep(5)
        return False

    # -- sandboxes ------------------------------------------------------------------

    def _acquire(self, isolated: bool) -> tuple[Any, bool]:
        """(handle, owned). Reuses the shared sandbox when allowed and free."""
        if self.opts.reuse_sandbox and not isolated:
            with self._lock:
                if self._shared is not None and not self._shared_busy:
                    self._shared_busy = True
                    return self._shared, False
                make_shared = self._shared is None
                if make_shared:
                    self._shared_busy = True
            if make_shared:
                try:
                    handle = self._create()
                except BaseException:
                    with self._lock:
                        self._shared_busy = False
                    raise
                with self._lock:
                    self._shared = handle
                return handle, False
        return self._create(), True        # isolated, reuse off, or shared one is in use

    def _release_shared(self) -> None:
        with self._lock:
            self._shared_busy = False

    def _create(self):
        from daytona import CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams

        assert self._source is not None
        kind, ref = self._source
        common: dict[str, Any] = {
            "labels": {"rlgate": "true", "rlgate-task": self.task_id[:63]},
            "auto_stop_interval": self.opts.auto_stop_minutes,
            "auto_delete_interval": 0,            # delete as soon as it stops
            "ttl_minutes": self.opts.ttl_minutes,
            "network_block_all": self.opts.network_block_all,
            "network_allow_list": self.opts.network_allow_list,
        }
        if kind == "snapshot":
            params = CreateSandboxFromSnapshotParams(snapshot=ref, **common)
        else:
            params = CreateSandboxFromImageParams(image=ref, resources=self.opts.resources(),
                                                  **common)
        need = self._size()
        if self._budget is not None:
            self._budget.room(*need, wait_s=self.opts.room_wait_s)
        try:
            handle = self._create_with_retry(params)
        finally:
            if self._budget is not None:
                self._budget.started(*need)
        with self._lock:
            self._live[str(getattr(handle, "id", id(handle)))] = handle
        with _OPEN_GUARD:
            _OPEN.add(self)      # keep alive until close(), so atexit can always clean up
        return handle

    def _size(self) -> tuple[float, float, float]:
        """(vCPU, GiB memory, GiB disk) one sandbox asks for: the options, else Daytona's
        documented per-sandbox defaults (1 vCPU, 1 GiB, 3 GiB)."""
        o = self.opts
        return (float(o.cpus or 1), float(o.memory_gb or 1), float(o.disk_gb or 3))

    def _create_with_retry(self, params):
        delay, attempt = 2.0, 1
        while True:
            # A create can time out on our side yet finish on Daytona's: tag each attempt so
            # a failed one can be found and deleted instead of running until auto-stop.
            tag = uuid.uuid4().hex[:16]
            params.labels = {**(params.labels or {}), "rlgate-create": tag}
            try:
                return self._client.create(params, timeout=self.opts.create_timeout_s)
            except Exception as e:
                self._reap(tag)
                if attempt >= self.opts.create_retries or not _is_transient(e):
                    raise DaytonaUnavailable(f"could not create a Daytona sandbox for "
                                             f"{self.task_id}: {type(e).__name__}: {e}") from e
                log.warning("sandbox create failed (%s); retry %d/%d in %.0fs", e, attempt,
                            self.opts.create_retries - 1, delay)
                time.sleep(delay)
                delay, attempt = delay * 2, attempt + 1

    def _reap(self, tag: str) -> None:
        """Delete sandboxes a failed create attempt may have left behind (best effort)."""
        try:
            page = self._client.list()
            items = getattr(page, "items", page) or []
        except Exception as e:  # noqa: BLE001 - offline etc.; auto-stop/TTL are the backstop
            log.warning("could not check for a half-created sandbox (%s); auto-stop in %d "
                        "min will remove it", e, self.opts.auto_stop_minutes)
            return
        for sb in items:
            if (getattr(sb, "labels", None) or {}).get("rlgate-create") == tag:
                log.warning("deleting sandbox %s left by a failed create", getattr(sb, "id", "?"))
                try:
                    self._client.delete(sb)
                except Exception as e:  # noqa: BLE001
                    log.warning("could not delete %s: %s", getattr(sb, "id", "?"), e)

    def _delete(self, handle, tracked: bool = True) -> None:
        if tracked:
            with self._lock:
                self._live.pop(str(getattr(handle, "id", id(handle))), None)
                if handle is self._shared:
                    self._shared, self._shared_busy = None, False
        try:
            self._client.delete(handle)
        except Exception as e:  # noqa: BLE001 - auto-stop/TTL clean up what this misses
            log.warning("could not delete sandbox %s: %s", getattr(handle, "id", "?"), e)

    def _load_workspace(self, handle, host_copy: Path) -> None:
        """Replace the remote workdir with a fresh copy of ``host_copy``."""
        tar_path = host_copy.with_suffix(".tgz")
        try:
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(host_copy, arcname=".")
            handle.fs.upload_file(str(tar_path), _REMOTE_TAR)
        finally:
            tar_path.unlink(missing_ok=True)
        wd = self.opts.workdir
        res = handle.process.exec(
            f"rm -rf {wd} && mkdir -p {wd} && tar xzf {_REMOTE_TAR} -C {wd} "
            f"&& rm -f {_REMOTE_TAR}", timeout=300)
        if res.exit_code != 0:
            raise DaytonaUnavailable(f"workspace extract failed: {res.result}")
