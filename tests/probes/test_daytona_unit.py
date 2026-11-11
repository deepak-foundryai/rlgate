"""Daytona backend behaviour against a fake client (no network): image resolution, snapshot
caching, one-sandbox-per-task reuse, isolated overlays, retries, crash-safety params."""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("daytona")

from daytona.common import errors as de

from rlgate.core.task import GraderSpec, Task
from rlgate.sandbox.daytona import (
    DaytonaOptions,
    DaytonaSandbox,
    DaytonaUnavailable,
)
from rlgate.sandbox.select import BackendUnavailable, build_sandbox


class FakeHandle:
    _ids = itertools.count()

    def __init__(self, params) -> None:
        self.id = f"sb-{next(self._ids)}"
        self.params = params
        self.cmds: list[str] = []
        self.uploads: list[str] = []
        self.process = SimpleNamespace(exec=self._exec)
        self.fs = SimpleNamespace(upload_file=lambda local, remote: self.uploads.append(remote))

    def _exec(self, cmd, cwd=None, timeout=None):
        self.cmds.append(cmd)
        return SimpleNamespace(exit_code=0, result="ok")


class FakeSnapshots:
    def __init__(self, state: str | None = None) -> None:
        self.states: dict[str, str] = {}
        self.created: list = []
        self.preset = state
        self.forbid = False

    def get(self, name):
        state = self.states.get(name, self.preset)
        if state is None:
            raise de.DaytonaNotFoundError("nope")
        return SimpleNamespace(state=state)

    def create(self, params, on_logs=None, timeout=None):
        if self.forbid:
            raise de.DaytonaForbiddenError("snapshots not allowed on this plan")
        self.created.append(params)
        self.states[params.name] = "active"

    def delete(self, name):
        self.states.pop(name, None)

    def activate(self, name):
        self.states[name] = "active"


class FakeClient:
    def __init__(self, fail_creates: int = 0, error=de.DaytonaRateLimitError) -> None:
        self.snapshot = FakeSnapshots()
        self.handles: list[FakeHandle] = []
        self.deleted: list[str] = []
        self._fail = fail_creates
        self._error = error
        self.orphans: list = []
        self.orphan_on_fail = False

    def create(self, params, timeout=None):
        if self._fail:
            self._fail -= 1
            if self.orphan_on_fail:          # timed out for us, but Daytona made it anyway
                self.orphans.append(SimpleNamespace(id=f"orphan-{self._fail}",
                                                    labels=dict(params.labels)))
            raise self._error("transient")
        h = FakeHandle(params)
        self.handles.append(h)
        return h

    def delete(self, handle):
        self.deleted.append(handle.id)

    def list(self):
        return SimpleNamespace(items=list(self.orphans))


def _task(tmp_path: Path, dockerfile: bool = True, image: str | None = None) -> Task:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n")
    img = image
    if dockerfile:
        env = tmp_path / "environment"
        env.mkdir()
        (env / "Dockerfile").write_text("FROM python:3.12-slim\nWORKDIR /app\n")
        img = str(env)                       # a directory holding a Dockerfile (Harbor)
    return Task(id="t1", source="test", repo_dir=repo, grader=GraderSpec(run="true", image=img))


def _sandbox(task: Task, client: FakeClient, **opts) -> DaytonaSandbox:
    sb = DaytonaSandbox.from_task(task, opts)
    sb._client_factory = lambda: client
    sb.opts = DaytonaOptions(**{**opts, "create_retries": opts.get("create_retries", 3)})
    return sb


def test_harbor_dockerfile_builds_once_into_a_cached_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    task, client = _task(tmp_path), FakeClient()
    sb = _sandbox(task, client)
    sb.build()
    assert sb._source[0] == "snapshot" and sb._source[1].startswith("rlgate-")
    assert len(client.snapshot.created) == 1

    sb2 = _sandbox(task, client)            # same environment → reuse, no rebuild
    sb2.build()
    assert sb2._source == sb._source and len(client.snapshot.created) == 1
    sb.close()
    sb2.close()


def test_snapshot_name_changes_when_the_environment_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    task, client = _task(tmp_path), FakeClient()
    a = _sandbox(task, client)
    a.build()
    (tmp_path / "environment" / "Dockerfile").write_text("FROM python:3.13-slim\n")
    b = _sandbox(task, client)
    b.build()
    assert a._source[1] != b._source[1]
    a.close()
    b.close()


def test_no_snapshot_permission_falls_back_to_building_the_image(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    client.snapshot.forbid = True
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    kind, ref = sb._source
    assert kind == "image" and "python:3.12-slim" in ref.dockerfile()
    sb.close()


def test_inactive_snapshot_is_activated_not_waited_on(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    client.snapshot.preset = "inactive"
    sb = _sandbox(_task(tmp_path), client, build_timeout_s=1)
    sb.build()
    assert sb._source[0] == "snapshot" and not client.snapshot.created
    sb.close()


def test_one_sandbox_per_task_with_a_fresh_workspace_each_grade(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    for _ in range(3):
        with sb.overlay(None) as ovl:
            ovl.exec("true")
    assert len(client.handles) == 1
    h = client.handles[0]
    resets = [c for c in h.cmds if c.startswith("rm -rf /work")]
    assert len(resets) == 3                 # workdir wiped + re-extracted every grade
    assert client.deleted == []             # shared sandbox survives between grades
    sb.close()
    assert client.deleted == [h.id]


def test_isolated_overlay_gets_its_own_sandbox_and_deletes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with sb.overlay(None):
        pass
    with sb.overlay(None, isolated=True):
        assert len(client.handles) == 2
    from rlgate.sandbox import daytona as mod
    mod.settle_deletes()                    # deletion runs in the background
    assert client.deleted == [client.handles[1].id]
    sb.close()


def test_concurrent_overlays_never_share_a_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with sb.overlay(None) as a, sb.overlay(None) as b:
        assert a._sb is not b._sb
    sb.close()


def test_crash_safety_params_on_every_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client, auto_stop_minutes=15, ttl_minutes=60,
                  network_block_all=True)
    sb.build()
    with sb.overlay(None):
        pass
    p = client.handles[0].params
    assert (p.auto_stop_interval, p.auto_delete_interval, p.ttl_minutes) == (15, 0, 60)
    assert p.network_block_all is True
    assert p.labels["rlgate"] == "true" and p.labels["rlgate-task"] == "t1"
    sb.close()


def test_transient_create_errors_are_retried(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    monkeypatch.setattr("rlgate.sandbox.daytona.time.sleep", lambda s: None)
    client = FakeClient(fail_creates=2)
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with sb.overlay(None):
        pass
    assert len(client.handles) == 1
    sb.close()


def test_auth_errors_are_not_retried(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient(fail_creates=1, error=de.DaytonaAuthenticationError)
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with pytest.raises(DaytonaUnavailable, match="DaytonaAuthenticationError"):
        sb.overlay(None)
    sb.close()


def test_registry_image_and_default_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_SNAPSHOT", "")    # empty, so a local .env can't refill it
    client = FakeClient()
    sb = _sandbox(_task(tmp_path, dockerfile=False, image="ghcr.io/org/env:1"), client)
    sb.build()
    assert sb._source == ("image", "ghcr.io/org/env:1")
    sb.close()
    (tmp_path / "b").mkdir()
    sb = _sandbox(_task(tmp_path / "b", dockerfile=False), client)
    sb.build()
    assert sb._source == ("image", "debian:12.9")
    sb.close()


def test_unknown_option_is_rejected_with_the_valid_list(tmp_path):
    task = _task(tmp_path)
    with pytest.raises(ValueError, match="unknown daytona option.*cpuz.*valid"):
        DaytonaOptions.from_kwargs({"cpuz": 2})
    with pytest.raises(BackendUnavailable, match="only apply to --backend daytona"):
        build_sandbox(task, prefer="local", options={"cpus": 2})


def test_global_snapshot_never_overrides_a_task_dockerfile(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_SNAPSHOT", "some-global")
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    assert sb._source[1].startswith("rlgate-")      # the task's own environment wins
    sb.close()


def test_global_snapshot_is_the_default_for_imageless_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_SNAPSHOT", "some-global")
    client = FakeClient()
    client.snapshot.states["some-global"] = "active"
    sb = _sandbox(_task(tmp_path, dockerfile=False), client)
    sb.build()
    assert sb._source == ("snapshot", "some-global")
    sb.close()


def test_missing_snapshot_fails_at_build_with_its_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_SNAPSHOT", "forge-283")
    sb = _sandbox(_task(tmp_path, dockerfile=False), FakeClient())
    with pytest.raises(DaytonaUnavailable, match="forge-283.*DAYTONA_SNAPSHOT.*does not exist"):
        sb.build()
    sb.close()


def test_unclosed_sandbox_is_still_cleaned_up_at_exit(tmp_path, monkeypatch):
    """Regression: a dropped (un-closed) DaytonaSandbox must stay reachable for atexit."""
    import gc

    from rlgate.sandbox import daytona as mod

    monkeypatch.setenv("DAYTONA_SNAPSHOT", "")
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with sb.overlay(None):
        pass
    del sb
    gc.collect()
    mod._close_all()
    assert client.deleted == [client.handles[0].id]
    assert not mod._OPEN


# -- staged backend: machine factories ----------------------------------------------------


def test_compose_env_gets_a_docker_in_docker_machine_with_task_resources(tmp_path, monkeypatch):
    from rlgate.core.task import EnvSpec

    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    spec = EnvSpec(compose=tmp_path / "docker-compose.yaml", cpus=8, memory_mb=14336,
                   storage_mb=20480, build_timeout_s=2700)
    fleet = DaytonaSandbox.for_env(spec, "t1", {"network_block_all": True})
    fleet._client_factory = lambda: client
    fleet.build()
    m = fleet.machine()
    h = client.handles[0]
    assert fleet.dind and h.params.image == "docker:28.3.3-dind"
    assert h.params.network_block_all is False            # dockerd must pull images
    assert any("dockerd" in c for c in h.cmds) and any("docker info" in c for c in h.cmds)
    r = h.params.resources
    assert (r.cpu, r.memory, r.disk) == (8, 14, 20)
    assert fleet.opts.build_timeout_s == 2700
    m.close()
    from rlgate.sandbox import daytona as mod
    mod.settle_deletes()
    assert client.deleted == [h.id]
    fleet.close()


def test_image_env_machine_is_a_dedicated_sandbox_and_explicit_options_win(tmp_path,
                                                                           monkeypatch):
    from rlgate.core.task import EnvSpec

    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    env = tmp_path / "tests"
    env.mkdir()
    (env / "Dockerfile").write_text("FROM python:3.12-slim\n")
    client = FakeClient()
    fleet = DaytonaSandbox.for_env(EnvSpec(dockerfile=env / "Dockerfile", cpus=8),
                                   "t1", {"cpus": 2, "snapshot": "ignored"})
    fleet._client_factory = lambda: client
    fleet.build()
    assert fleet._source[0] == "snapshot" and fleet._source[1].startswith("rlgate-")
    assert fleet.opts.cpus == 2                       # --ek beats task.toml
    a, b = fleet.machine("/grading"), fleet.machine("/grading")
    assert a.handle is not b.handle and a.workdir == "/grading"
    a.exec("true")
    assert client.handles[0].cmds[-1] == "true"
    fleet.close()
    assert len(client.deleted) == 2


def test_a_create_that_times_out_but_lands_is_deleted(tmp_path, monkeypatch):
    """Regression (live run): Daytona's create exceeded our timeout yet finished, leaving a
    running sandbox nothing tracked."""
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    monkeypatch.setattr("rlgate.sandbox.daytona.time.sleep", lambda s: None)
    client = FakeClient(fail_creates=1, error=de.DaytonaTimeoutError)
    client.orphan_on_fail = True
    sb = _sandbox(_task(tmp_path), client)
    sb.build()
    with sb.overlay(None):
        pass
    assert client.deleted[0] == "orphan-0"
    sb.close()


class _Proc:
    """A sandbox process API with background sessions, a flaky network and a slow command."""

    def __init__(self, polls_before_done=2, fail_polls=1, exit_code=0):
        self.left, self.fail, self.code = polls_before_done, fail_polls, exit_code
        self.started, self.deleted, self.execs = [], [], []

    def exec(self, cmd, cwd=None, timeout=None):
        self.execs.append(cmd)
        return SimpleNamespace(exit_code=0, result="short")

    def create_session(self, sid, request_timeout=None):
        pass

    def execute_session_command(self, sid, req, timeout=None):
        self.started.append(req.command)
        return SimpleNamespace(cmd_id="c1")

    def get_session_command(self, sid, cid, request_timeout=None):
        from daytona.common.errors import DaytonaConnectionError
        if self.fail:
            self.fail -= 1
            raise DaytonaConnectionError("Connection reset by peer")
        if self.left:
            self.left -= 1
            return SimpleNamespace(exit_code=None)
        return SimpleNamespace(exit_code=self.code)

    def get_session_command_logs(self, sid, cid, request_timeout=None):
        return SimpleNamespace(output="long output", stdout=None, stderr=None)

    def delete_session(self, sid, request_timeout=None):
        self.deleted.append(sid)


def test_long_commands_run_in_the_background_and_survive_a_dropped_poll(monkeypatch):
    pytest.importorskip("daytona")
    from rlgate.sandbox import daytona as d
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    p = _Proc(exit_code=3)
    r = d._run(SimpleNamespace(process=p), "make test", cwd="/app", timeout=900)
    assert (r.exit_code, r.stdout) == (3, "long output")
    assert len(p.started) == 1 and "cd /app && make test" in p.started[0]   # started once
    assert "timeout -k 10 900" in p.started[0] and p.deleted and not p.execs


def test_short_commands_are_one_request(monkeypatch):
    pytest.importorskip("daytona")
    from rlgate.sandbox import daytona as d
    p = _Proc()
    assert d._run(SimpleNamespace(process=p), "true", cwd=None, timeout=30).stdout == "short"
    assert p.execs == ["true"] and not p.started


def test_a_command_past_its_deadline_reports_a_timeout(monkeypatch):
    pytest.importorskip("daytona")
    from rlgate.sandbox import daytona as d
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(d.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    p = _Proc(polls_before_done=10_000, fail_polls=0)
    r = d._run(SimpleNamespace(process=p), "sleep 9999", cwd=None, timeout=120)
    assert r.exit_code == 124 and "timed out" in r.stdout and p.deleted


def test_the_sandbox_cap_waits_then_goes_over_and_frees_slots_on_delete(tmp_path, monkeypatch):
    from rlgate.sandbox import daytona as mod
    monkeypatch.delenv("DAYTONA_SNAPSHOT", raising=False)
    client = FakeClient()
    sb = _sandbox(_task(tmp_path), client, max_sandboxes=7001, room_wait_s=0.05)
    sb.build()
    sem = mod._slots(7001)
    held = [sem.acquire(timeout=0) for _ in range(7000)]       # leave one slot free
    a = sb.machine()
    b = sb.machine()                       # no slot left: waits 0.05s, then goes over
    assert len(client.handles) == 2 and sem.acquire(timeout=0) is False
    a.close()
    mod.settle_deletes()
    assert sem.acquire(timeout=0) is True  # a's slot came back; b never held one
    b.close()
    sb.close()
    for _ in range(sum(held) + 1):
        sem.release()
