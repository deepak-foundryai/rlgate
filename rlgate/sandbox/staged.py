"""The staged backend: tasks whose workspace lives INSIDE an image, and/or whose grader runs
in a SEPARATE environment from the agent (Harbor-format tasks; any loader can describe one
with ``Task.workspace_env`` / ``GraderSpec.env``).

Build, once per task:
  1. bring up the workspace environment and pull its workdir out to the host (dependency
     trees such as ``node_modules`` skipped, except inside an artifact) — this host copy
     becomes the task's ``repo_dir``, so every probe and variant works on the real files;
     pristine copies of artifacts outside the workdir are pulled too;
  2. prepare the grading environment (build or pull its image, or build its compose stack).

Each overlay (one candidate) then gets a FRESH grading environment:
  - separate grader: the candidate's artifacts — and only those, exactly as Harbor carries
    them — are copied in (directories replaced wholesale), then ``run`` executes there;
  - shared grader: a fresh workspace container gets only the files the candidate changed
    (untouched files keep the image's own owners and modes).
Held-out material (``GraderSpec.inject``, e.g. ``tests/`` → ``/tests``) is uploaded in
``before_grade`` — after anything the candidate did, never visible to it before.

``oracle_overlay`` grades the reference SOLUTION SCRIPT the way the harness does (run it in
the workspace environment, carry its effects to the grader); ``derive_gold`` turns that
run's workdir changes into the gold patch the variant probes need.

Backends: ``daytona`` (each environment a Daytona sandbox; compose stacks inside one
Docker-in-Docker sandbox per task) and ``docker`` (a local daemon; compose via the host's
``docker compose``). The pieces they share are ``Machine``s (``sandbox.machine``).
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import yaml

from rlgate.core.task import EnvSpec, Task
from rlgate.sandbox._image import context_hash
from rlgate.sandbox._patch import apply_zero_fuzz
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox
from rlgate.sandbox.errors import BackendUnavailable, BuildFailed, is_build_error
from rlgate.sandbox.machine import (
    HEAVY_DIRS,
    ComposeMachine,
    DockerMachine,
    HostMachine,
    Machine,
    MachineError,
    changed_files,
    download_dir,
    push_delta,
    remote_kind,
    upload_dir,
    upload_file,
)

log = logging.getLogger(__name__)


class StagedUnavailable(BackendUnavailable):
    """The task can't be run on this backend; the message says why and what to do."""


# -- environment providers ----------------------------------------------------------------


class EnvProvider(ABC):
    """Hands out fresh, running instances of one ``EnvSpec``."""

    def __init__(self, spec: EnvSpec) -> None:
        self.spec = spec

    @abstractmethod
    def provision(self) -> None:
        """Build/pull once."""

    @abstractmethod
    def fresh(self) -> Machine:
        """A pristine running instance; the caller closes it."""

    def close(self) -> None:
        """Release everything (idempotent)."""


class DaytonaImageEnv(EnvProvider):
    def __init__(self, spec: EnvSpec, task_id: str, options: dict | None) -> None:
        super().__init__(spec)
        from rlgate.sandbox.daytona import DaytonaSandbox
        self.fleet = DaytonaSandbox.for_env(spec, task_id, options)
        if not spec.network:
            import dataclasses
            self.fleet.opts = dataclasses.replace(self.fleet.opts, network_block_all=True)

    def provision(self) -> None:
        self.fleet.build()

    def fresh(self) -> Machine:
        return self.fleet.machine(self.spec.workdir)

    def close(self) -> None:
        self.fleet.close()


class DockerImageEnv(EnvProvider):
    """A local Docker daemon: build (or pull) once, one container per ``fresh``."""

    def __init__(self, spec: EnvSpec, task_id: str) -> None:
        super().__init__(spec)
        self.host = HostMachine()
        self.task_id = task_id
        self.tag: str | None = None
        self._live: list[DockerMachine] = []

    def provision(self) -> None:
        s = self.spec
        if s.dockerfile is not None:
            self.tag = f"rlgate-{_slug(self.task_id)}:{context_hash(s.dockerfile)[:12]}"
            if self.host.exec(f"docker image inspect {self.tag}", timeout=60).exit_code != 0:
                self.host.check(
                    f"docker build -t {self.tag} -f {shlex.quote(str(s.dockerfile))} "
                    f"{shlex.quote(str(s.dockerfile.parent))}",
                    what=f"docker build {s.dockerfile}", timeout=int(s.build_timeout_s))
        else:
            assert s.image, "an image env needs a Dockerfile or an image"
            self.tag = s.image
            if self.host.exec(f"docker image inspect {shlex.quote(s.image)}",
                              timeout=60).exit_code != 0:
                self.host.check(f"docker pull {shlex.quote(s.image)}",
                                what=f"docker pull {s.image}", timeout=int(s.build_timeout_s))

    def fresh(self) -> Machine:
        assert self.tag, "provision() first"
        s = self.spec
        flags = ["-d", "--entrypoint", "sleep", "--label", "rlgate=true"]
        if not s.network:
            flags.append("--network=none")
        if s.cpus:
            flags.append(f"--cpus={s.cpus}")
        if s.memory_mb:
            flags.append(f"--memory={s.memory_mb}m")
        cid = self.host.check(f"docker run {' '.join(flags)} {self.tag} infinity",
                              what="docker run", timeout=300).strip()
        m = DockerMachine(cid, self.host, s.workdir)
        self._live.append(m)
        return m

    def close(self) -> None:
        for m in self._live:
            m.close()
        self._live.clear()


class StackHosts:
    """The Docker hosts compose stacks run on.

    On Daytona each host is a Docker-in-Docker sandbox sized for ONE stack, as the harness
    runs a task, so a stack gets a host to itself: the pool grows on demand up to ``cap``
    hosts (each builds the task's images once). Past that a stack waits for a free host. A
    thread that already holds a host never waits for a second one (that could wait forever);
    it shares the least busy host instead. Locally there is one host (this machine's
    daemon) and stacks share it."""

    def __init__(self, make: Callable[[], Machine], cap: int, *, exclusive: bool = True,
                 wait_log_s: float = 120) -> None:
        self._make = make
        self.cap = max(1, cap)
        self.exclusive = exclusive
        self._wait_log_s = wait_log_s
        self._hosts: list[_StackHost] = []
        self._cond = threading.Condition()
        self._held = threading.local()
        self._closed = False

    @property
    def size(self) -> int:
        return len(self._hosts)

    def lease(self) -> _StackHost:
        held = getattr(self._held, "n", 0)
        waited = time.monotonic()
        with self._cond:
            while True:
                if self._closed:
                    raise MachineError("the task's stack hosts were closed")
                ready = [h for h in self._hosts if h.machine is not None]
                free = [h for h in ready if h.busy == 0]
                if free or (not self.exclusive and ready):
                    h = min(free or ready, key=lambda h: h.busy)
                    break
                if len(self._hosts) < self.cap:
                    h = _StackHost(len(self._hosts))
                    self._hosts.append(h)        # reserved; made outside the lock
                    break
                if held and ready:
                    h = min(ready, key=lambda h: h.busy)
                    log.info("stack host %d shared: this thread already holds one", h.n)
                    break
                if not self._cond.wait(timeout=self._wait_log_s):
                    log.info("waiting %.0fs for a free stack host (%d in use)",
                             time.monotonic() - waited, len(self._hosts))
            h.busy += 1
        if h.machine is None:
            try:
                log.info("starting stack host %d of at most %d", h.n + 1, self.cap)
                m = self._make()
            except BaseException:
                with self._cond:
                    self._hosts.remove(h)
                    self._cond.notify_all()
                raise
            with self._cond:
                h.machine = m
                self._cond.notify_all()
        self._held.n = held + 1
        return h

    def release(self, h: _StackHost) -> None:
        self._held.n = max(0, getattr(self._held, "n", 1) - 1)
        with self._cond:
            h.busy -= 1
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            hosts, self._hosts = self._hosts, []
            self._cond.notify_all()
        for h in hosts:
            if h.machine is not None:
                try:
                    h.machine.close()
                except Exception as e:  # noqa: BLE001 - cleanup must reach every host
                    log.warning("could not close a stack host: %s", e)


class _StackHost:
    def __init__(self, n: int) -> None:
        self.n = n
        self.machine: Machine | None = None
        self.busy = 0
        self.ready: set[str] = set()         # ComposeEnv keys whose images are built here
        self.prep = threading.Lock()


class ComposeEnv(EnvProvider):
    """A multi-service stack on a Docker host (local, or Docker-in-Docker sandboxes).

    Images are built once per host under fixed tags (an override file names them), so every
    ``fresh`` is a new compose project brought up from those images with no rebuild — each
    candidate gets its own database and services, torn down (``down -v``) when it closes."""

    _counter = 0
    _counter_lock = threading.Lock()

    def __init__(self, spec: EnvSpec, key: str, hosts: StackHosts) -> None:
        super().__init__(spec)
        assert spec.compose is not None
        self.key = _slug(key)[:40]
        self.hosts = hosts
        self.remote_dir = f"/rlgate/{self.key}"
        rd = self.remote_dir
        self._base = (f"docker compose --project-directory {rd} -f {rd}/.rlgate-base.yaml "
                      f"-f {rd}/{spec.compose.name} -f {rd}/.rlgate-override.yaml")

    def provision(self) -> None:
        """Build on the first host now, so a broken build is reported by step 1."""
        h = self.hosts.lease()
        try:
            self._ready_on(h)
        finally:
            self.hosts.release(h)

    def _ready_on(self, h: _StackHost) -> None:
        with h.prep:
            if self.key in h.ready:
                return
            assert h.machine is not None
            self._setup(h.machine)
            h.ready.add(self.key)

    def _setup(self, host: Machine) -> None:
        s = self.spec
        assert s.compose is not None
        upload_dir(host, s.compose.parent, self.remote_dir, replace=True)
        layers = {".rlgate-base.yaml": _compose_base(s),
                  ".rlgate-override.yaml": _compose_override(s.compose, self.key, s.service,
                                                             prebuilt=bool(s.image))}
        tmp = Path(tempfile.mkdtemp(prefix="rlgate-compose-"))
        try:
            for name, doc in layers.items():
                (tmp / name).write_text(yaml.safe_dump(doc, sort_keys=False))
                upload_file(host, tmp / name, f"{self.remote_dir}/{name}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        host.check(f"{self._base} -p rlgate-{self.key}-build build",
                   what=f"compose build ({s.compose})", timeout=int(s.build_timeout_s))

    def fresh(self) -> Machine:
        with ComposeEnv._counter_lock:
            ComposeEnv._counter += 1
            n = ComposeEnv._counter
        cmd = f"{self._base} -p rlgate-{self.key}-{n}"
        h = self.hosts.lease()
        released = threading.Event()

        def down() -> None:
            try:
                if h.machine is not None:
                    h.machine.exec(f"{cmd} down -v --remove-orphans --timeout 5", timeout=300)
            finally:
                if not released.is_set():
                    released.set()
                    self.hosts.release(h)

        try:
            self._ready_on(h)
        except BaseException:
            released.set()
            self.hosts.release(h)
            raise
        host = h.machine
        assert host is not None
        log.debug("compose up on host %d: %s", h.n + 1, cmd)
        up = host.exec(f"{cmd} up -d --no-build", timeout=900)
        if up.exit_code != 0:
            # a service that fails to start once (a database's first-boot race, a slow
            # disk) is retried once from scratch before the stack counts as broken
            state = _stack_state(host, cmd)
            log.info("compose up failed on host %d, retrying once:%s\n%s", h.n + 1, state,
                     (up.stdout + up.stderr)[-1500:])
            host.exec(f"{cmd} down -v --remove-orphans --timeout 5", timeout=300)
            up = host.exec(f"{cmd} up -d --no-build", timeout=900)
        if up.exit_code != 0:
            state = _stack_state(host, cmd)
            down()
            raise MachineError(f"compose up failed twice:{state}\n"
                               f"{(up.stdout + up.stderr)[-2000:]}")
        m = ComposeMachine(host, cmd, self.spec.service, self.spec.workdir, on_close=down)
        for _ in range(60):
            if m.exec("true", timeout=30).exit_code == 0:
                return m
            time.sleep(2)
        state = _stack_state(host, cmd)
        m.close()
        raise MachineError(f"compose service {self.spec.service!r} never became ready"
                           + state)

    def close(self) -> None:
        pass                # the hosts are owned (and closed) by the sandbox


def _stack_state(host: Machine, cmd: str) -> str:
    """Why a stack's containers stopped, for the error message: each one that is not
    running, with its exit code and whether the kernel killed it for memory."""
    r = host.exec(f"for c in $({cmd} ps -aq); do docker inspect -f "
                  "'{{.Name}} {{.State.Status}} exit={{.State.ExitCode}} "
                  "oom={{.State.OOMKilled}}' \"$c\"; done", timeout=120)
    down = [ln.strip().lstrip("/") for ln in r.stdout.splitlines()
            if ln.strip() and " running " not in ln]
    if not down:
        return ""
    oom = any("oom=true" in ln for ln in down)
    why = (" — the task's containers ran out of memory; give the sandbox more "
           "(--backend-opt memory_gb=N)" if oom else "")
    return "\nstopped containers: " + "; ".join(down) + why


def _compose_base(spec: EnvSpec) -> dict:
    """What the harness layers UNDER a task's compose file: ``main`` built from the
    compose file's directory (or the prebuilt image), idling so commands can exec in."""
    main: dict = {"command": ["sh", "-c", "sleep infinity"]}
    if spec.image:
        main["image"] = spec.image
    else:
        main["build"] = {"context": "."}
    return {"services": {spec.service: main}}


def _compose_override(compose: Path, key: str, main: str, prebuilt: bool = False) -> dict:
    """Layered LAST: fixed image tags for every built service, so the stack builds once and
    each candidate's project comes up from those images with no rebuild."""
    doc = yaml.safe_load(compose.read_text()) or {}
    services = dict(doc.get("services") or {})
    services.setdefault(main, {})
    out: dict = {}
    for name, svc in services.items():
        svc = svc or {}
        built = "build" in svc or (name == main and not prebuilt)
        if built and not svc.get("image"):
            out[name] = {"image": f"rlgate-{key}-{_slug(name)}:latest"}
    return {"services": out or {main: {}}}


# -- the sandbox ------------------------------------------------------------------------


class StagedOverlay(Overlay):
    def __init__(self, machine: Machine, root: Path, cwd: str | None, workdir: str | None,
                 inject: tuple[tuple[str, str], ...], extra: list[Machine] | None = None) -> None:
        self.m = machine
        self.root = root
        self.cwd = cwd
        self.workdir = workdir
        self._inject = inject
        self._extra = extra or []
        self._staged = False

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        return self.m.exec(cmd, cwd=self.cwd, timeout=timeout)

    def before_grade(self) -> None:
        if self._staged:
            return
        self._staged = True
        for host_dir, remote in self._inject:
            upload_dir(self.m, Path(host_dir), remote)          # merged in, as Harbor does
        self.m.exec("mkdir -p /logs/verifier", timeout=60)

    def read_perms(self, path: str) -> Perms:
        target = path if path.startswith("/") else f"{self.workdir or '.'}/{path}"
        r = self.m.exec(f"stat -c %a {shlex.quote(target)}", timeout=60)
        if r.exit_code != 0:
            return Perms(mode=0, exists=False)
        try:
            return Perms(mode=int(r.stdout.strip().splitlines()[-1], 8))
        except (ValueError, IndexError):
            return Perms(mode=0, exists=False)

    def close(self) -> None:
        for m in [self.m, *self._extra]:
            try:
                m.close()
            except Exception as e:  # noqa: BLE001 - best effort; TTLs are the backstop
                log.warning("could not release a grading environment: %s", e)
        self._extra = []
        shutil.rmtree(self.root, ignore_errors=True)


class StagedSandbox(Sandbox):
    def __init__(self, task: Task, backend: str, options: dict | None = None) -> None:
        if backend not in ("daytona", "docker"):
            raise StagedUnavailable(
                f"{task.id}: this task's workspace and grader live in container images — it "
                f"needs --backend daytona or docker, not {backend!r}")
        self.task = task
        self.backend = backend
        self.options = options
        g = task.grader
        self.ws_spec = task.workspace_env
        self.grader_spec = g.env
        self.separate = g.env is not None
        self.workdir = (self.ws_spec.workdir if self.ws_spec else None) or None
        self.artifacts = tuple(g.artifacts)
        self.workspace_dir: Path | None = None       # host copy of the workdir (repo_dir)
        self._outside: Path | None = None            # pristine artifacts outside the workdir
        self._ws_env: EnvProvider | None = None
        self._grader_env: EnvProvider | None = None
        self._stack_hosts: StackHosts | None = None  # compose: where the stacks run
        self._hosts_lock = threading.Lock()
        self._oracle_capture: Path | None = None     # separate: artifacts after solve.sh
        self._oracle_ws: Path | None = None          # workdir after solve.sh
        self._oracle_log: str = ""
        self.oracle_exit: int | None = None     # the solution script's last exit code
        self._whole: set[str] = set()                # artifacts pulled with their deps
        self._tmp: list[Path] = []                   # per-run captures, removed at close
        self.solution_seconds: list[float] = []      # every reference-solution run

    # -- lifecycle ------------------------------------------------------------------

    def build(self) -> None:
        if self.ws_spec is None:
            raise StagedUnavailable(f"{self.task.id}: no workspace environment declared")
        if self.workdir is None:
            raise StagedUnavailable(
                f"{self.task.id}: can't tell where the workspace is inside the image — set "
                "[environment] workdir in task.toml (or WORKDIR in the Dockerfile)")
        failures: list[tuple[str, str]] = []
        self._ws_env = self._provider(self.ws_spec, "ws")
        self._provision(self._ws_env, "the agent's environment", failures)
        if self.separate:
            assert self.grader_spec is not None
            if self.grader_spec == self.ws_spec:
                self._grader_env = self._ws_env      # a fresh copy of the agent environment
            else:
                self._grader_env = self._provider(self.grader_spec, "grader")
                self._provision(self._grader_env, "the verifier's environment", failures)
        else:
            self._grader_env = self._ws_env
        if failures:                                 # every image was tried; report them all
            raise BuildFailed(failures)
        m = self._ws_env.fresh()
        try:
            self.workspace_dir = self._pull_workspace(m)
            self._outside = self._pull_outside(m)
        finally:
            m.close()

    @staticmethod
    def _provision(env: EnvProvider, label: str, failures: list[tuple[str, str]]) -> None:
        try:
            env.provision()
        except BuildFailed as e:
            failures.extend((f"{label}: {what}", log) for what, log in e.failures)
        except MachineError as e:
            if not is_build_error(e):
                raise
            failures.append((f"{label} ({env.spec.describe})", str(e)))

    def close(self) -> None:
        for env in {id(e): e for e in (self._grader_env, self._ws_env) if e}.values():
            try:
                env.close()
            except Exception as e:  # noqa: BLE001
                log.warning("environment cleanup failed: %s", e)
        if self._stack_hosts is not None:
            self._stack_hosts.close()
            self._stack_hosts = None
        for p in (self.workspace_dir, self._outside, self._oracle_capture, self._oracle_ws,
                  *self._tmp):
            if p is not None:
                shutil.rmtree(p, ignore_errors=True)
        self._tmp = []
        self.workspace_dir = self._outside = self._oracle_capture = self._oracle_ws = None

    def pkill_user(self, user: str) -> None:  # every grade is a fresh environment
        pass

    # -- overlays -------------------------------------------------------------------

    def overlay(self, patch_text: str | None = None, *,
                isolated: bool = False) -> StagedOverlay:
        # every overlay is a fresh environment, so `isolated` always holds
        assert self.workspace_dir is not None and self._grader_env is not None, \
            "call build() first"
        ws = _copy_tree(self.workspace_dir, "rlgate-sovl-")
        try:
            if patch_text:
                apply_zero_fuzz(ws, patch_text)
            if self.separate:
                m = self._grader_env.fresh()
                try:
                    self._carry_artifacts(m, ws, self._outside)
                except BaseException:
                    m.close()
                    raise
            else:
                m = self._grader_env.fresh()
                try:
                    written, deleted = changed_files(self.workspace_dir, ws)
                    push_delta(m, ws, self.workdir or "/", written, deleted)
                except BaseException:
                    m.close()
                    raise
        except BaseException:
            shutil.rmtree(ws, ignore_errors=True)
            raise
        return StagedOverlay(m, ws, self._grade_cwd(), self.workdir,
                             self.task.grader.inject)

    def oracle_overlay(self, fresh: bool = False) -> StagedOverlay:
        """Grade what the reference solution SCRIPT produces, the way the harness does.
        ``fresh`` re-runs the script from scratch (each baseline run after the first)."""
        if self.task.oracle is None:
            raise StagedUnavailable(f"{self.task.id}: no solution script")
        if self.separate and not fresh:
            self._run_oracle_once()
            assert self._oracle_capture is not None and self._oracle_ws is not None
            return self._separate_overlay(self._oracle_ws, self._oracle_capture)
        return self.agent_overlay(self._solution_cmd(), timed=True, strict=True)

    def agent_overlay(self, cmd: str, *, timed: bool = False, strict: bool = False,
                      patch: str | None = None) -> StagedOverlay:
        """Run ``cmd`` in a fresh agent environment as the agent would (in the workdir), then
        hand its work to the grader exactly as the harness does: separate → only the
        artifacts cross into a fresh verifier; shared → grading happens right there.
        ``patch`` is applied to the workspace first, as if the agent had edited those files."""
        assert self._ws_env is not None and self._grader_env is not None, "call build() first"
        m = self._ws_env.fresh()
        try:
            if patch:
                assert self.workspace_dir is not None and self.workdir is not None
                edited = _copy_tree(self.workspace_dir, "rlgate-sovl-")
                self._tmp.append(edited)
                apply_zero_fuzz(edited, patch)
                written, deleted = changed_files(self.workspace_dir, edited)
                push_delta(m, edited, self.workdir, written, deleted)
            self._agent_exec(m, cmd, timed=timed, strict=strict)
            if not self.separate:
                assert self.workspace_dir is not None
                return StagedOverlay(m, _copy_tree(self.workspace_dir, "rlgate-sovl-"),
                                     self._grade_cwd(), self.workdir, self.task.grader.inject)
            ws, cap = self._capture(m)
        except BaseException:
            m.close()
            raise
        m.close()
        self._tmp += [ws, cap]
        return self._separate_overlay(ws, cap)

    def agent_env(self) -> StagedOverlay:
        """A fresh agent environment, exactly as the agent first sees it (nothing staged)."""
        assert self._ws_env is not None, "call build() first"
        m = self._ws_env.fresh()
        return StagedOverlay(m, Path(tempfile.mkdtemp(prefix="rlgate-agent-")), self.workdir,
                             self.workdir, ())

    def _separate_overlay(self, ws_src: Path, cap: Path) -> StagedOverlay:
        assert self._grader_env is not None
        ws = _copy_tree(ws_src, "rlgate-sovl-")
        m = self._grader_env.fresh()
        try:
            self._carry_artifacts(m, ws, cap, from_capture=True)
        except BaseException:
            m.close()
            shutil.rmtree(ws, ignore_errors=True)
            raise
        return StagedOverlay(m, ws, self._grade_cwd(), self.workdir, self.task.grader.inject)

    def derive_gold(self) -> str | None:
        """The solution script's effect on the workdir, as a patch (None if it changed
        nothing there — e.g. it only writes outside the workspace)."""
        if self.task.oracle is None:
            return None
        self._run_oracle_once()
        assert self.workspace_dir is not None and self._oracle_ws is not None
        if self.oracle_exit:          # a half-run solution is no reference fix
            return None
        return tree_patch(self.workspace_dir, self._oracle_ws)

    @property
    def oracle_log(self) -> str:
        return self._oracle_log

    # -- internals ------------------------------------------------------------------

    def _provider(self, spec: EnvSpec, role: str) -> EnvProvider:
        key = f"{self.task.id}-{role}"
        if spec.compose is not None:
            return ComposeEnv(spec, key, self._hosts())
        if self.backend == "daytona":
            return DaytonaImageEnv(spec, self.task.id, self.options)
        return DockerImageEnv(spec, self.task.id)

    def _hosts(self) -> StackHosts:
        """Where compose stacks run: this machine's daemon, or Docker-in-Docker sandboxes
        each sized for one stack (``stack_hosts`` of them at most, default 4)."""
        with self._hosts_lock:
            if self._stack_hosts is not None:
                return self._stack_hosts
            if self.backend == "docker":
                self._stack_hosts = StackHosts(HostMachine, 1, exclusive=False)
                return self._stack_hosts
            from rlgate.sandbox.daytona import DaytonaOptions, DaytonaSandbox
            specs = [s for s in (self.ws_spec, self.grader_spec) if s is not None]
            biggest = max(specs, key=lambda s: (s.memory_mb or 0, s.cpus or 0))
            opts = DaytonaOptions.from_kwargs(self.options)
            fleet = DaytonaSandbox.for_env(
                EnvSpec(compose=biggest.compose or Path("docker-compose.yaml"),
                        cpus=max((s.cpus or 0) for s in specs) or None,
                        memory_mb=max((s.memory_mb or 0) for s in specs) or None,
                        storage_mb=max((s.storage_mb or 0) for s in specs) or None,
                        build_timeout_s=max(s.build_timeout_s for s in specs)),
                self.task.id, opts)
            built = threading.Lock()
            state = {"built": False}

            def make() -> Machine:
                with built:
                    if not state["built"]:
                        fleet.build()
                        state["built"] = True
                return fleet.machine()

            pool = StackHosts(make, opts.stack_hosts)
            outer = pool.close

            def close() -> None:
                outer()
                fleet.close()           # deletes every sandbox the fleet made
            pool.close = close          # type: ignore[method-assign]
            self._stack_hosts = pool
            return pool

    def _pull_workspace(self, m: Machine) -> Path:
        assert self.workdir is not None
        snap = Path(tempfile.mkdtemp(prefix="rlgate-ws-"))
        download_dir(m, self.workdir, snap, exclude=HEAVY_DIRS)
        # an artifact that IS (or holds) a dependency tree is carried whole, so pull it whole
        for a in self._artifacts_under_workdir():
            rel = _rel(a, self.workdir)
            if remote_kind(m, a) == "dir" and m.exec(
                    f"find {shlex.quote(a)} -type d \\( "
                    + " -o ".join(f"-name {d}" for d in sorted(HEAVY_DIRS))
                    + " \\) -print -quit", timeout=120).stdout.strip():
                download_dir(m, a, snap / rel)
                self._whole.add(a)
        return snap

    def _pull_outside(self, m: Machine) -> Path:
        out = Path(tempfile.mkdtemp(prefix="rlgate-outside-"))
        for a in self.artifacts:
            if self.workdir and _under(a, self.workdir):
                continue
            _pull_path(m, a, out / a.lstrip("/"))
        return out

    def _artifacts_under_workdir(self) -> list[str]:
        return [a for a in self.artifacts if self.workdir and _under(a, self.workdir)]

    def _carry_artifacts(self, m: Machine, ws: Path, outside: Path | None,
                         from_capture: bool = False) -> None:
        """Harbor semantics: each artifact that exists is uploaded to its own path; a
        directory REPLACES what the grader image had there."""
        assert self.workdir is not None
        for a in self.artifacts:
            if _under(a, self.workdir) and not from_capture:
                src = ws / _rel(a, self.workdir)
            else:
                if outside is None:
                    continue
                src = outside / a.lstrip("/")
            if src.is_dir() and not src.is_symlink():
                upload_dir(m, src, a, replace=True)
            elif src.exists():
                upload_file(m, src, a)

    def _run_oracle_once(self) -> None:
        if self._oracle_ws is not None:
            return
        assert self._ws_env is not None and self.workdir is not None
        m = self._ws_env.fresh()
        try:
            self._agent_exec(m, self._solution_cmd(), timed=True, strict=True)
            self._oracle_ws, self._oracle_capture = self._capture(m)
        finally:
            m.close()

    def _capture(self, m: Machine) -> tuple[Path, Path]:
        """(the workdir, the artifacts) of an environment after a run — what the harness
        would carry to a separate verifier."""
        assert self.workdir is not None
        ws = Path(tempfile.mkdtemp(prefix="rlgate-run-ws-"))
        download_dir(m, self.workdir, ws, exclude=HEAVY_DIRS)
        for a in sorted(self._whole):           # same rule as the pristine pull
            shutil.rmtree(ws / _rel(a, self.workdir), ignore_errors=True)
            _pull_path(m, a, ws / _rel(a, self.workdir))
        cap = Path(tempfile.mkdtemp(prefix="rlgate-run-cap-"))
        for a in self.artifacts:
            if _under(a, self.workdir):
                src = ws / _rel(a, self.workdir)
                dst = cap / a.lstrip("/")
                if src.is_dir():
                    shutil.copytree(src, dst, symlinks=True)
                elif src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            else:
                _pull_path(m, a, cap / a.lstrip("/"))
        return ws, cap

    def _solution_cmd(self) -> str:
        assert self.task.oracle is not None
        _, script = self.task.oracle
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self.task.meta.get(
            "solution_env", {}).items())
        prefix = f"export {env}; " if env else ""
        return f"{prefix}chmod +x /solution/{script}; /solution/{script}"

    def _agent_exec(self, m: Machine, cmd: str, *, timed: bool, strict: bool) -> str:
        """Run ``cmd`` where the agent works (the solution dir is uploaded for scripts)."""
        if self.task.oracle is not None and "/solution/" in cmd:
            upload_dir(m, Path(self.task.oracle[0]), "/solution", replace=True)
        timeout = int(self.task.meta.get("agent_timeout_s", 3600))
        t = time.monotonic()
        r = m.exec(cmd, cwd=self.workdir, timeout=timeout)
        if timed:
            self.solution_seconds.append(time.monotonic() - t)
        out = (r.stdout + r.stderr)[-4000:]
        if strict:
            # The harness grades whatever the solution left behind, whatever its exit code;
            # a failing solution is the task's defect (RG001 from the grade), not a reason to
            # call the task unchecked.
            self.oracle_exit = r.exit_code
            if r.exit_code != 0:
                # reported as RG001 with the grade; the output goes to rlgate.log only
                log.info("%s: the reference solution exited %d:\n%s", self.task.id,
                            r.exit_code, out[-1500:])
        if timed:
            self._oracle_log = out
        return out

    def _grade_cwd(self) -> str | None:
        g = self.task.grader
        if g.cwd:
            return g.cwd
        spec = self.grader_spec if self.separate else self.ws_spec
        return (spec.workdir if spec else None) or self.workdir


# -- helpers ----------------------------------------------------------------------------


def tree_patch(base: Path, new: Path) -> str | None:
    """A ``-p1`` git patch turning ``base`` into ``new`` (binary-safe), or None if equal."""
    written, deleted = changed_files(base, new)
    if not written and not deleted:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-tp-"))
    try:
        for side, root, rels in (("a", base, written + deleted), ("b", new, written)):
            (tmp / side).mkdir()
            for rel in rels:
                src = root / rel
                if src.is_symlink() or src.is_file():
                    dst = tmp / side / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst, follow_symlinks=False)
        # a text file that isn't UTF-8 (Latin-1 source, a .po file...) is diffed as binary,
        # so the patch stays ASCII (base85) and still applies byte for byte
        odd = sorted({rel for side, rels in (("a", written + deleted), ("b", written))
                      for rel in rels if not _utf8(tmp / side / rel)})
        attrs = tmp / ".attrs"
        attrs.write_text("".join(f"{_attr_glob(r)} binary\n" for r in odd),
                         encoding="utf-8")
        proc = subprocess.run(
            ["git", "-c", f"core.attributesFile={attrs}", "diff", "--no-index",
             "--no-prefix", "--binary", "--no-color", "--no-ext-diff", "--no-renames",
             "a", "b"],
            cwd=tmp, capture_output=True, check=False)
        if proc.returncode not in (0, 1):
            raise RuntimeError("git diff failed: "
                               + proc.stderr.decode("utf-8", "replace").strip())
        try:
            return proc.stdout.decode("utf-8") or None
        except UnicodeDecodeError as e:
            raise RuntimeError(
                "the reference solution's changes can't be turned into a text patch "
                f"(a file name that isn't UTF-8?): {e}") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _utf8(p: Path) -> bool:
    if p.is_symlink() or not p.is_file():
        return True
    try:
        p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _attr_glob(rel: str) -> str:
    """A gitattributes pattern matching exactly ``rel`` (glob characters escaped)."""
    pat = "**/" + "".join("\\" + c if c in "*?[]\\!#" else c for c in rel)
    if any(c.isspace() or c == '"' for c in pat):      # C-style quoted pattern
        pat = '"' + pat.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return pat


def _pull_path(m: Machine, remote: str, local: Path) -> None:
    kind = remote_kind(m, remote)
    if kind == "dir":
        download_dir(m, remote, local)
    elif kind == "file":
        local.parent.mkdir(parents=True, exist_ok=True)
        m.get_file(remote, local)


def _copy_tree(src: Path, prefix: str) -> Path:
    dst = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def _under(path: str, root: str) -> bool:
    p, r = PurePosixPath(path), PurePosixPath(root)
    return p == r or r in p.parents


def _rel(path: str, root: str) -> str:
    rel = PurePosixPath(path).relative_to(PurePosixPath(root)).as_posix()
    return "" if rel == "." else rel


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")
    return out or uuid.uuid4().hex[:8]


__all__ = [
    "ComposeEnv",
    "DaytonaImageEnv",
    "DockerImageEnv",
    "EnvProvider",
    "StagedOverlay",
    "StagedSandbox",
    "StagedUnavailable",
    "tree_patch",
]
