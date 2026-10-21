"""Live Daytona-backend smoke test.

Skips unless BOTH: the backend is available (DAYTONA_API_KEY + SDK installed) AND
RLGATE_DAYTONA_LIVE=1 is set — so the normal suite and CI never spin up (or bill) a real
cloud sandbox by accident. Run it on demand with:

    RLGATE_DAYTONA_LIVE=1 pytest tests/probes/test_daytona_backend.py -q
"""

import os

import pytest

from rlgate.sandbox.daytona import available

_LIVE = os.environ.get("RLGATE_DAYTONA_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    not (_LIVE and available()),
    reason="set RLGATE_DAYTONA_LIVE=1 and configure DAYTONA_API_KEY + install daytona",
)


def test_overlay_exec_and_perms(tmp_path):
    from rlgate.core.task import GraderSpec, Task
    from rlgate.sandbox.daytona import DaytonaSandbox

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "held.txt").write_text("secret\n")
    (repo / "check.sh").write_text("echo ok\n")

    task = Task(id="dt", source="test", repo_dir=repo,
                grader=GraderSpec(run="sh check.sh", image="daytona"))
    sb = DaytonaSandbox.from_task(task)
    sb.build()
    with sb.overlay(None) as ovl:
        res = ovl.exec("cat tests/held.txt")
        assert res.exit_code == 0 and "secret" in res.stdout
        assert ovl.read_perms("tests").exists
