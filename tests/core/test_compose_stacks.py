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


def test_a_full_account_gets_no_extra_hosts_only_slower_stacks(monkeypatch):
    # a compose task's extra DinD hosts (16 GB each) pushed a shared account over its
    # memory limit; without room the stacks queue on the hosts the task already has
    monkeypatch.setattr(StackHosts, "ROOM_POLL_S", 0.01)
    made: list[_Host] = []

    def make():
        made.append(_Host())
        return made[-1]
    room = [False]
    env = _env(StackHosts(make, cap=3, can_grow=lambda: room[0]))

    def grade():
        m = env.fresh()
        time.sleep(0.05)
        m.close()
    _run(4, grade)
    assert len(made) == 1 and made[0].peak == 1          # the first host is always made
    room[0] = True
    _run(4, grade)
    assert len(made) == 3                                # room again: grows to the cap


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


def test_a_command_that_prompts_gets_end_of_input_instead_of_waiting(tmp_path):
    from rlgate.sandbox.local import LocalOverlay
    r = LocalOverlay(tmp_path).exec("read -r answer; echo \"got:[$answer] rc=$?\"", timeout=10)
    assert "got:[] rc=0" in r.stdout or "got:[]" in r.stdout


def test_daytona_long_commands_get_no_input(monkeypatch):
    """A Daytona session keeps its stdin open; the command must still see end-of-input."""
    import subprocess
    from types import SimpleNamespace

    from rlgate.sandbox import daytona
    monkeypatch.setattr(daytona.time, "sleep", lambda s: None)
    ran = {}

    class Proc:
        def create_session(self, sid, request_timeout=60):
            pass

        def execute_session_command(self, sid, req, timeout=60):
            # run it as a session would: stdin is a pipe that stays open
            import os
            rd, wr = os.pipe()                      # the write end stays open: no EOF
            try:
                import signal
                p = subprocess.Popen(["sh", "-c", req.command], stdin=rd,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, start_new_session=True)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)     # the whole group, so nothing lingers
                    p.wait()
                    ran["hung"] = True
                ran.update(code=p.returncode, out=p.stdout.read() if p.stdout else "")
            finally:
                os.close(rd)
                os.close(wr)
            return SimpleNamespace(cmd_id="c")

        def get_session_command(self, sid, cmd_id, request_timeout=30):
            return SimpleNamespace(exit_code=ran["code"])

        def get_session_command_logs(self, sid, cmd_id, request_timeout=120):
            return SimpleNamespace(output=ran["out"])

        def delete_session(self, sid, request_timeout=30):
            pass

    r = daytona._run(SimpleNamespace(process=Proc()), 'read -r a; echo "got:[$a]"',
                     cwd=None, timeout=600)
    assert "hung" not in ran and "got:[]" in r.stdout


def test_log_lines_name_their_task_even_from_worker_threads(tmp_path):
    import logging

    from rlgate.core.jobs import TASK, Job
    from rlgate.variants.engine import pmap
    job = Job.open(tmp_path, target=str(tmp_path), name="t", options={}, argv=[])
    log = logging.getLogger("rlgate.core.grade")
    try:
        for task in ("tb2-regex-log", "tb2-fix-git"):
            token = TASK.set(task)
            try:
                pmap(lambda i: log.info("graded in 1.0s: exit %d", i), [0, 1, 2], 3)
            finally:
                TASK.reset(token)
    finally:
        job.close()
    lines = [ln for ln in (job.path / "rlgate.log").read_text().splitlines() if "graded" in ln]
    assert len(lines) == 6
    assert sum("[tb2-regex-log]" in ln for ln in lines) == 3
    assert sum("[tb2-fix-git]" in ln for ln in lines) == 3


def test_run_folders_are_named_target_then_date_and_time(tmp_path):
    import re

    from rlgate.core.jobs import Job
    a = Job.open(tmp_path, target="/x/terminal-bench-2", name=None, options={}, argv=[])
    b = Job.open(tmp_path, target="/x/terminal-bench-2", name=None, options={}, argv=[])
    c = Job.open(tmp_path, target="/x/t", name="tb2", options={}, argv=[])
    for j in (a, b, c):
        j.close()
    assert re.fullmatch(r"terminal-bench-2_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}", a.path.name)
    assert b.path.name == a.path.name + "-2"            # same minute: numbered, not clobbered
    assert c.path.name.startswith("tb2_")


def test_a_run_of_the_current_folder_is_named_after_it(tmp_path, monkeypatch):
    from rlgate.core.jobs import Job
    bank = tmp_path / "foundryai-coding-data"
    bank.mkdir()
    monkeypatch.chdir(bank)
    j = Job.open(tmp_path / "jobs", target=".", name=None, options={}, argv=[])
    j.close()
    assert j.path.name.startswith("foundryai-coding-data_")


def test_a_service_that_never_answers_is_restarted_once_then_says_why(monkeypatch):
    from rlgate.sandbox import staged
    monkeypatch.setattr(staged.time, "sleep", lambda s: None)

    class Slow(_Host):
        ups = 0
        answers_after = 2               # the service answers once the stack is restarted

        def exec(self, cmd, *, cwd=None, timeout=900):
            if " up -d " in cmd:
                Slow.ups += 1
            elif " exec " in cmd and Slow.ups < Slow.answers_after:
                return ExecResult(1, "", "service \"main\" is not running")
            return super().exec(cmd, cwd=cwd, timeout=timeout)
    _env(StackHosts(Slow, cap=1)).fresh().close()
    assert Slow.ups == 2

    Slow.ups, Slow.answers_after = 0, 99
    hosts = StackHosts(Slow, cap=1)
    with pytest.raises(MachineError) as e:
        _env(hosts).fresh()
    assert Slow.ups == 2 and 'service "main" is not running' in str(e.value)
    h = hosts.lease()
    assert h.busy == 1
    hosts.release(h)


def test_a_stack_host_lost_while_idle_is_replaced(monkeypatch):
    made: list = []

    class Host(_Host):
        def __init__(self):
            super().__init__()
            self.gone = False
            made.append(self)

        def alive(self):
            return not self.gone

        def close(self):
            pass

    monkeypatch.setattr(StackHosts, "RECHECK_IDLE_S", 0)
    hosts = StackHosts(Host, cap=1)
    hosts.release(hosts.lease())
    made[0].gone = True                           # the backend removed it while idle
    h = hosts.lease()
    assert h.machine is made[1] and len(made) == 2   # a replacement, not a failed task
    hosts.release(h)
    hosts.close()


def test_every_open_daytona_sandbox_is_kept_alive_until_closed(monkeypatch):
    import gc

    from rlgate.sandbox import daytona as D

    class Handle:
        beats = 0

        def refresh_activity(self):
            Handle.beats += 1

    class Fleet:
        def _delete(self, handle):
            pass
    monkeypatch.setattr(D, "KEEPALIVE_S", 0.01)
    monkeypatch.setattr(D, "_BEAT", {"thread": None})
    m = D.DaytonaMachine(Handle(), Fleet())          # type: ignore[arg-type]
    time.sleep(0.1)
    assert Handle.beats > 0
    m.close()
    gc.collect()
    n = Handle.beats
    time.sleep(0.1)
    assert Handle.beats == n                       # a closed sandbox is left alone


def test_ctrl_c_deletes_the_runs_sandboxes_and_exits_at_once(monkeypatch):
    import os

    from rlgate import cli
    from rlgate.sandbox import daytona as D
    deleted = []

    class Fleet:
        def _delete(self, handle):
            deleted.append(handle)

    class Handle:
        def refresh_activity(self):
            pass
    m = D.DaytonaMachine(Handle(), Fleet())          # type: ignore[arg-type]

    def interrupted(*a, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "app", interrupted)

    def exit_now(code):
        raise SystemExit(code)
    monkeypatch.setattr(os, "_exit", exit_now)
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "."])
    assert e.value.code == 130 and deleted == [m.handle]


_STOP = """
import os, signal, sys, threading, time
import rlgate.cli as c
threading.Thread(target=time.sleep, args=(60,)).start()     # a busy, non-daemon worker
def app(**kw):
    sig = signal.SIGINT if sys.argv[1] == "ctrl-c" else signal.SIGTERM
    for t in (0.5, 0.5, 0.52):                  # twice: uv run forwards kill; double Ctrl-C
        threading.Timer(t, os.kill, (os.getpid(), sig)).start()
    time.sleep(60)
c.app = app
sys.exit(c.main(["check", "x"]))
"""


@pytest.mark.parametrize("how", ["ctrl-c", "term"])
def test_ctrl_c_and_kill_exit_at_once_with_workers_still_busy(how):
    # Typer turns Ctrl-C into a plain return of 130, so the cleanup never ran and the
    # interpreter then waited for the busy worker threads: the run hung instead of stopping
    import subprocess
    import sys
    import time
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-c", _STOP, how], capture_output=True, text=True,
                       timeout=30, check=False)
    assert p.returncode == 130, p.stderr
    assert "Interrupted" in p.stderr and time.monotonic() - t0 < 20


def test_readiness_does_not_depend_on_a_workdir_made_later(monkeypatch):
    # a grader stack's workdir (/grading) appears only when the tests are uploaded: probing
    # readiness there failed every time ("chdir to cwd ... no such file") and the task
    # was reported as "compose service 'main' never became ready"
    from rlgate.sandbox import staged
    monkeypatch.setattr(staged.time, "sleep", lambda s: None)

    class NoWorkdirYet(_Host):
        made = False

        def exec(self, cmd, *, cwd=None, timeout=900):
            if "mkdir -p /grading" in cmd:
                NoWorkdirYet.made = True
            if " exec " in cmd and "-w /grading " in cmd and not NoWorkdirYet.made:
                return ExecResult(127, "", 'chdir to cwd ("/grading") set in config.json '
                                  "failed: no such file or directory")
            return super().exec(cmd, cwd=cwd, timeout=timeout)
    env = ComposeEnv(EnvSpec(compose=Path("docker-compose.yaml"), workdir="/grading"),
                     "t-gr", StackHosts(NoWorkdirYet, cap=1))
    env._setup = lambda host: None          # type: ignore[method-assign]
    m = env.fresh()
    assert m.workdir == "/grading"          # commands still run there once it exists
    assert m.exec("true").exit_code == 0     # (uploads into the service, say)
    m.close()


def test_a_long_task_ids_workspace_and_grader_stacks_get_different_names():
    # cut to 40 characters, "lostly-cross-notification-delivery-outcomes-workspace" and
    # "...-grader" were the same name: one image tag, one folder, and a grader that came up
    # from the agent's image (no /grading, no /tests)
    from rlgate.sandbox.staged import _stack_key
    tid = "lostly-cross-notification-delivery-outcomes"
    a, b = _stack_key(f"{tid}-workspace"), _stack_key(f"{tid}-grader")
    assert a != b and len(a) <= 40 and len(b) <= 40 and a.startswith("lostly-cross")
    assert _stack_key("ast-ops-canary-decides-early-grader") == \
        "ast-ops-canary-decides-early-grader"          # short names are unchanged
    assert _stack_key(f"{tid}x-grader") != b           # nor do two long task ids collide
