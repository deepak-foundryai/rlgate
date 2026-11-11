"""Compose stacks on Docker-in-Docker hosts: one live stack per host (each is sized for one
stack), hosts added on demand up to a cap, never a wait that can't end; and a stopped
service explains itself instead of Docker's "Could not find the file"."""

import threading
import time
from pathlib import Path

import pytest

from rlgate.sandbox.base import ExecResult
from rlgate.sandbox.machine import ComposeMachine, MachineError
from rlgate.sandbox.staged import ComposeEnv, EnvSpec, StackHosts


class _Host:
    def __init__(self, inspect: str = "running exit=0 oom=false") -> None:
        self.live = self.peak = 0
        self.lock = threading.Lock()
        self.inspect = inspect

    def exec(self, cmd: str, *, cwd=None, timeout: int = 900) -> ExecResult:
        if " up -d " in cmd:
            with self.lock:
                self.live += 1
                self.peak = max(self.peak, self.live)
            time.sleep(0.05)
        elif " down " in cmd:
            with self.lock:
                self.live -= 1
        elif cmd.startswith("docker inspect"):
            return ExecResult(0, self.inspect, "")
        elif " cp " in cmd:
            return ExecResult(1, "", "Error response from daemon: Could not find the file /tmp")
        return ExecResult(0, "", "")

    def check(self, cmd: str, *, what: str = "", timeout: int = 900) -> str:
        r = self.exec(cmd, timeout=timeout)
        if r.exit_code != 0:
            raise MachineError(f"{what} failed (exit {r.exit_code}):\n{r.stderr}")
        return r.stdout

    def put_file(self, local, remote) -> None:
        pass


def _env(hosts: StackHosts) -> ComposeEnv:
    env = ComposeEnv(EnvSpec(compose=Path("docker-compose.yaml")), "t-gr", hosts)
    env._setup = lambda host: None          # type: ignore[method-assign]
    return env


def _run(n, work):
    ts = [threading.Thread(target=work) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in ts), "a stack waited forever"


def test_each_stack_gets_its_own_host_up_to_the_cap():
    made: list[_Host] = []

    def make():
        made.append(_Host())
        return made[-1]
    hosts = StackHosts(make, cap=3)
    env = _env(hosts)

    def grade():
        m = env.fresh()
        time.sleep(0.1)
        m.close()
    _run(8, grade)
    assert len(made) == 3                               # grew to the cap, no further
    assert all(h.peak == 1 and h.live == 0 for h in made)   # one live stack per host


def test_a_thread_holding_a_host_never_waits_for_another():
    hosts = StackHosts(_Host, cap=1)
    env = _env(hosts)

    def nested():
        outer = env.fresh()                 # e.g. the hack agent's live environment
        inner = env.fresh()                 # ...then a grading inside the same thread
        inner.close()
        outer.close()
    _run(1, nested)


def test_the_images_are_built_once_per_host():
    built = []
    hosts = StackHosts(_Host, cap=2)
    env = ComposeEnv(EnvSpec(compose=Path("docker-compose.yaml")), "t-gr", hosts)
    env._setup = lambda host: built.append(host)   # type: ignore[method-assign]
    env.provision()
    ms = [env.fresh(), env.fresh()]        # the second needs a second host
    for m in ms:
        m.close()
    env.fresh().close()
    assert len(built) == 2 and built[0] is not built[1]


def test_a_failed_host_frees_its_place():
    calls = []

    def make():
        calls.append(1)
        if len(calls) == 1:
            raise MachineError("daytona said no")
        return _Host()
    hosts = StackHosts(make, cap=1)
    env = _env(hosts)
    with pytest.raises(MachineError):
        env.fresh()
    env.fresh().close()
    assert hosts.size == 1


def test_locally_stacks_share_this_machine():
    host = _Host()
    env = _env(StackHosts(lambda: host, cap=1, exclusive=False))
    ms = [env.fresh() for _ in range(3)]
    assert host.peak == 3
    for m in ms:
        m.close()


def test_a_stopped_service_says_why(tmp_path):
    host = _Host(inspect="exited exit=137 oom=true")
    m = ComposeMachine(host, "docker compose -p x", "main")
    f = tmp_path / "f"
    f.write_text("x")
    with pytest.raises(MachineError) as e:
        m.put_file(f, "/tmp/x.tgz")
    assert "not running (exited exit=137 oom=true)" in str(e.value)
    assert "memory_gb" in str(e.value)


def test_every_step_is_logged_with_its_time_and_failure(caplog):
    from rlgate import ui
    caplog.set_level("INFO", logger="rlgate.run")
    with ui.step("Build every image"):
        pass
    with pytest.raises(RuntimeError), ui.step("Run the reference solution"):
        raise RuntimeError("compose cp in failed")
    text = caplog.text
    assert "start: Build every image" in text and "done in" in text
    assert "failed after" in text and "compose cp in failed" in text


def test_a_task_that_could_not_run_still_shows_its_time():
    from pathlib import Path

    from rlgate.core.report import TaskView
    from rlgate.render import run_label
    v = TaskView("t", "t", Path("."), None, None, error="boom", seconds=272.0)
    assert "0.0s" not in run_label(v)


def test_a_reference_change_to_a_non_utf8_file_becomes_an_ascii_patch(tmp_path):
    import subprocess

    from rlgate.sandbox.staged import tree_patch
    base, new = tmp_path / "base", tmp_path / "new"
    for root, word in ((base, b"one"), (new, b"two")):
        (root / "deep dir").mkdir(parents=True)
        (root / "deep dir" / "café [1].txt").write_bytes(b"caf\xe9 \x95 " + word + b"\n")
        (root / "ok.py").write_text("x = 1\n" if word == b"one" else "x = 2\n")
    patch = tree_patch(base, new)
    assert patch is not None
    assert "GIT binary patch" in patch and "-x = 1" in patch
    work = tmp_path / "work"
    subprocess.run(["cp", "-R", str(base), str(work)], check=True)
    (work / "p.diff").write_text(patch, encoding="utf-8")
    subprocess.run(["git", "apply", "-p1", "p.diff"], cwd=work, check=True)
    assert (work / "deep dir" / "café [1].txt").read_bytes() == b"caf\xe9 \x95 two\n"


def test_the_terminal_gets_one_line_and_the_log_gets_everything(tmp_path):
    import logging

    from rlgate.core.jobs import Job, _OneLine
    job = Job.open(tmp_path, target=str(tmp_path), name="t", options={}, argv=[])
    records = []
    grab = logging.Handler()
    grab.emit = records.append                       # type: ignore[method-assign]
    log = logging.getLogger("rlgate.run")
    log.addHandler(grab)
    try:
        log.warning("task-x: the solution said:\nline two\nline three")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("task-y could not be checked")
    finally:
        log.removeHandler(grab)
        job.close()
    shown = [_OneLine().format(r) for r in records]
    assert shown == ["task-x: the solution said: (details in rlgate.log)",
                     "task-y could not be checked (details in rlgate.log)"]
    text = (job.path / "rlgate.log").read_text()
    assert "line three" in text and "Traceback" in text and "ValueError: boom" in text


def test_a_command_printing_non_utf8_bytes_does_not_crash(tmp_path):
    from rlgate.sandbox.local import LocalOverlay
    r = LocalOverlay(tmp_path).exec("printf 'caf\\351 \\225 ok'; printf '\\377' >&2; exit 3")
    assert r.exit_code == 3 and "ok" in r.stdout


def test_changes_to_git_internals_are_not_parts_of_the_fix():
    from rlgate.core.task import Patch
    from rlgate.variants.revert import revert_variants
    patch = Patch(
        "--- a/.git/ORIG_HEAD\n+++ b/.git/ORIG_HEAD\n@@ -1 +1 @@\n-aaa\n+bbb\n"
        "--- a/app/main.py\n+++ b/app/main.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "--- a/app/more.py\n+++ b/app/more.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n")
    origins = [v.origin for v in revert_variants(patch)]
    assert origins == ["app/main.py#hunk0", "app/more.py#hunk0"]


def test_a_stack_that_fails_to_start_once_is_retried():
    class Flaky(_Host):
        ups = 0

        def exec(self, cmd, *, cwd=None, timeout=900):
            if " up -d " in cmd:
                Flaky.ups += 1
                if Flaky.ups == 1:
                    return ExecResult(1, "", "dependency failed to start: mongo exited (48)")
            return super().exec(cmd, cwd=cwd, timeout=timeout)
    env = _env(StackHosts(Flaky, cap=1))
    env.fresh().close()
    assert Flaky.ups == 2


def test_a_stack_that_fails_twice_says_which_container_stopped():
    class Broken(_Host):
        def exec(self, cmd, *, cwd=None, timeout=900):
            if " up -d " in cmd:
                return ExecResult(1, "", "dependency failed to start")
            if " ps -aq" in cmd:
                return ExecResult(0, "/x-mongo-1 exited exit=48 oom=false\n", "")
            return super().exec(cmd, cwd=cwd, timeout=timeout)
    hosts = StackHosts(Broken, cap=1)
    with pytest.raises(MachineError) as e:
        _env(hosts).fresh()
    assert "failed twice" in str(e.value) and "x-mongo-1 exited exit=48" in str(e.value)
    h = hosts.lease()                   # the host was released: a new lease doesn't wait
    assert h.busy == 1
    hosts.release(h)
