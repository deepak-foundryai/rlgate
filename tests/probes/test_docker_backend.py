"""Live Docker-backend smoke test. Skips cleanly when Docker is unavailable (CI without a
daemon, or this dev box), so the suite stays green everywhere."""

import pytest

from rlgate.sandbox.docker import available

pytestmark = pytest.mark.skipif(not available(), reason="docker not available")

BUSYBOX = "busybox:1.36"


def test_overlay_exec_and_perms(tmp_path):
    from rlgate.core.task import GraderSpec, Task
    from rlgate.sandbox.docker import DockerSandbox

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "held.txt").write_text("secret\n")

    task = Task(id="dock", source="test", repo_dir=repo,
                grader=GraderSpec(run="ls /work", image=BUSYBOX))
    sb = DockerSandbox.from_task(task)
    sb.build()
    with sb.overlay(None) as ovl:
        res = ovl.exec("cat tests/held.txt")
        assert res.exit_code == 0 and "secret" in res.stdout
        perms = ovl.read_perms("tests")
        assert perms.exists
