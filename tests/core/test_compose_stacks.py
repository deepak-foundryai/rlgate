"""Compose stacks on a Docker-in-Docker host: one at a time (each is sized for the whole
host), and a stopped service explains itself instead of Docker's "Could not find the file"."""

import threading
import time
from pathlib import Path

import pytest

from rlgate.sandbox.base import ExecResult
from rlgate.sandbox.machine import ComposeMachine, MachineError
from rlgate.sandbox.staged import ComposeEnv, EnvSpec


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


def _env(host, slots):
    env = ComposeEnv(EnvSpec(compose=Path("docker-compose.yaml")), "t-gr", lambda: host, slots)
    env.host, env._base = host, "docker compose"
    return env


def test_stacks_on_one_host_run_one_at_a_time():
    host = _Host()
    env = _env(host, threading.Semaphore(1))

    def grade():
        m = env.fresh()
        time.sleep(0.05)
        m.close()

    ts = [threading.Thread(target=grade) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert host.peak == 1 and host.live == 0


def test_without_slots_stacks_run_together():
    host = _Host()
    env = _env(host, None)
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
