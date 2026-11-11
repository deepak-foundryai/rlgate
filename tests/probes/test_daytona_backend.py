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


def test_overlay_exec_and_perms(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_SNAPSHOT", "")   # use the default image, not a global one
    from rlgate.core.task import GraderSpec, Task
    from rlgate.sandbox.daytona import DaytonaSandbox

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "held.txt").write_text("secret\n")
    (repo / "check.sh").write_text("echo ok\n")

    task = Task(id="dt", source="test", repo_dir=repo,
                grader=GraderSpec(run="sh check.sh"))
    with DaytonaSandbox.from_task(task) as sb:
        sb.build()
        with sb.overlay(None) as ovl:
            res = ovl.exec("cat tests/held.txt")
            assert res.exit_code == 0 and "secret" in res.stdout
            assert ovl.read_perms("tests").exists


def test_harbor_style_dockerfile_task_audits_end_to_end(tmp_path, capsys):
    """The regression this backend was rewritten for: a task whose grader needs its own
    image (environment/Dockerfile with pytest) must grade INSIDE that image on Daytona —
    gold passes, do-nothing fails, and the conftest forgery is proven and replayable."""
    import shlex
    import shutil

    from rlgate.cli import main
    from rlgate.ingest import spec
    from rlgate.pipeline import audit

    src = "tests/fixtures/forgery/conftest_hijack"
    shutil.copytree(f"{src}/repo", tmp_path / "repo",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(f"{src}/fix.patch", tmp_path / "fix.patch")
    (tmp_path / "environment").mkdir()
    (tmp_path / "environment" / "Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN pip install --no-cache-dir pytest==8.3.4\n")
    (tmp_path / "task.rlgate.yaml").write_text(
        "id: dt_conftest\nworkspace: ./repo\ngold_patch: ./fix.patch\n"
        "grader:\n  run: \"python -m pytest -q -p no:cacheprovider\"\n"
        "  image: ./environment\nlanguages: [python]\n")

    task = spec.load(tmp_path)
    result = audit(task, k=1, backend="daytona", only={"forgery.conftest_injection"})
    assert result.baseline_sane, [f.title for f in result.findings]
    [f] = [f for f in result.findings if f.rule_id == "RG201"]

    argv = shlex.split(f.evidence.replay)[1:] + ["--backend", "daytona"]
    assert main(argv) == 0
    assert "REPRODUCED" in capsys.readouterr().out
