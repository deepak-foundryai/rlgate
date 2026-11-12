"""SWE-bench-style rows: loaded natively and graded the way the SWE-bench harness grades."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rlgate.core.grade import run_grader
from rlgate.core.grade import tests_resolved as resolved
from rlgate.core.task import EnvSpec
from rlgate.ingest.bank import detect_layout, load_bank
from rlgate.sandbox.parsers import ReportParseError, TestCase, TestReport, TestStatus, parse_report
from rlgate.sandbox.parsers.swebench import spec_format

PYTEST = spec_format("parse_log_pytest")
BUGGY = "def add(a, b):\n    return a - b\n"
GOLD = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a - b\n+    return a + b\n")

# An eval script shaped like SWE-bench's: set -x, cd into the repo, apply the hidden tests,
# run them between the markers. The "test run" prints pytest -rA style lines.
EVAL = """#!/bin/bash
set -uxo pipefail
cd /testbed
cat > hidden_check.sh <<'EOF_1'
if grep -q 'a + b' calc.py; then echo "PASSED tests/test_calc.py::test_add"; s=0
else echo "FAILED tests/test_calc.py::test_add - assert -1 == 3"; s=1; fi
echo "PASSED tests/test_calc.py::test_old"
exit $s
EOF_1
: '>>>>> Start Test Output'
bash hidden_check.sh
: '>>>>> End Test Output'
rm -f hidden_check.sh
"""


def _row(**kw) -> dict:
    row = {"instance_id": "acme__calc-1", "repo": "acme/calc", "version": "1.0",
           "problem_statement": "add() subtracts", "patch": GOLD,
           "image": "swebench/sweb.eval.x86_64.acme_1776_calc-1:latest",
           "eval_script": EVAL, "log_parser": "parse_log_pytest", "eval_type": "pass_and_fail",
           "FAIL_TO_PASS": json.dumps(["tests/test_calc.py::test_add"]),
           "PASS_TO_PASS": json.dumps(["tests/test_calc.py::test_old"])}
    row.update(kw)
    return row


def _bank(tmp_path: Path, *rows: dict) -> Path:
    p = tmp_path / "bank" / "rows.jsonl"
    p.parent.mkdir()
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_rows_load_as_image_tasks_with_held_out_eval_scripts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    p = _bank(tmp_path, _row(), _row(image_name="other:v2", external_audit_id="calc-v2"))
    assert detect_layout(p) == "swebench" and detect_layout(p.parent) == "swebench"
    a, b = load_bank(p, preset="swebench")
    assert a.id == "acme__calc-1" and b.id == "calc-v2"
    assert a.workspace_env == EnvSpec(image="swebench/sweb.eval.x86_64.acme_1776_calc-1:latest",
                                      workdir="/testbed")
    assert b.workspace_env is not None and b.workspace_env.image == "other:v2"
    g = a.grader
    assert g.verdict == "tests" and g.fail_to_pass == ("tests/test_calc.py::test_add",)
    assert g.pass_to_pass == ("tests/test_calc.py::test_old",)
    (host, dest), = g.inject
    script = (Path(host) / "eval.sh").read_text()
    assert dest == "/rlgate-swe" and ">>>>> Test Exit Code" in script     # recorded, as SWE-bench
    assert a.gold_patch is not None and a.instruction == "add() subtracts"
    assert not list(a.repo_dir.iterdir())        # the workspace is in the image, not here
    assert "unsupported" not in a.meta
    assert "limits" not in a.meta            # the format can't declare them: no RG012


def test_an_old_row_without_an_eval_script_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    row = _row()
    del row["eval_script"], row["log_parser"]
    (t,) = load_bank(_bank(tmp_path, row), preset="swebench")
    assert any("eval_script" in u for u in t.meta["unsupported"])
    assert any("log_parser" in u for u in t.meta["unsupported"])


def test_the_same_instance_twice_gets_distinct_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ids = [t.id for t in load_bank(_bank(tmp_path, _row(), _row()), preset="swebench")]
    assert ids == ["acme__calc-1", "acme__calc-1#2"]


def _report(**status: str) -> TestReport:
    st = {"PASSED": TestStatus.PASS, "XFAIL": TestStatus.PASS, "FAILED": TestStatus.FAIL,
          "ERROR": TestStatus.FAIL, "SKIPPED": TestStatus.SKIP}
    return TestReport([TestCase(k.replace("__", "::"), st[v], v) for k, v in status.items()])


def test_resolution_follows_swebench():
    f2p, p2p = ("t__a",), ("t__b",)
    f2p, p2p = tuple(x.replace("__", "::") for x in f2p), tuple(x.replace("__", "::") for x in p2p)
    assert resolved(_report(t__a="PASSED", t__b="PASSED"), f2p, p2p)
    assert resolved(_report(t__a="XFAIL", t__b="SKIPPED"), f2p, p2p)   # P2P may skip
    assert not resolved(_report(t__a="SKIPPED", t__b="PASSED"), f2p, p2p)  # F2P can't
    assert not resolved(_report(t__b="PASSED"), f2p, p2p)              # missing = fail
    assert not resolved(_report(t__a="PASSED", t__b="ERROR"), f2p, p2p)
    # fail_only: only a listed test that FAILED counts against
    assert resolved(_report(t__b="PASSED"), f2p, p2p, "fail_only")
    assert not resolved(_report(t__a="FAILED"), f2p, p2p, "fail_only")
    # a pytest id truncated mid-parameter matches the tests it prefixes when they agree
    trunc = ("t::test_x[log(photon",)
    rep = TestReport([TestCase("t::test_x[log(photon)]", TestStatus.PASS, "PASSED")])
    assert resolved(rep, trunc, ())


def _log(body: str, code: int | None = None) -> str:
    tail = f">>>>> Test Exit Code: {code}\n" if code is not None else ""
    return f"+ : '>>>>> Start Test Output'\n{body}+ : '>>>>> End Test Output'\n{tail}"


def test_invalid_runs_are_no_report_at_all():
    ok = parse_report(PYTEST, _log("PASSED t.py::test_a\n", 0))
    assert ok.passed == ["t.py::test_a"]
    with pytest.raises(ReportParseError, match="Patch Apply Failed"):
        parse_report(PYTEST, ">>>>> Patch Apply Failed\n" + _log("PASSED t.py::test_a\n"))
    with pytest.raises(ReportParseError, match="markers"):
        parse_report(PYTEST, "PASSED t.py::test_a\n")
    # a submission printing its own PASSED lines while the test command failed
    with pytest.raises(ReportParseError, match="exited 1"):
        parse_report(PYTEST, _log("PASSED t.py::test_a\n", 1))
    with pytest.raises(ReportParseError, match="log parser"):
        parse_report("swebench:" + json.dumps({"parser": "parse_log_nope"}), _log(""))


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_a_row_is_graded_end_to_end_like_the_harness(tmp_path, monkeypatch):
    from tests.core.test_staged import FakeEnv, _sandbox
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (task,) = load_bank(_bank(tmp_path, _row()), preset="swebench")
    assert task.workspace_env is not None
    envs = {"ws": FakeEnv(task.workspace_env, {"/testbed/calc.py": BUGGY})}
    envs["grader"] = envs["ws"]
    with _sandbox(task, envs) as sb:
        sb.build()
        assert not (sb.workspace_dir / "hidden_check.sh").exists()   # tests are held out
        with sb.overlay(GOLD) as ovl:
            g = run_grader(ovl, task)
        assert g.passed, g.stdout + g.stderr
        assert g.report is not None and sorted(g.report.passed) == [
            "tests/test_calc.py::test_add", "tests/test_calc.py::test_old"]
        with sb.overlay(None) as ovl:
            g = run_grader(ovl, task)
        assert not g.passed and g.report is not None
        assert g.report.failed == ["tests/test_calc.py::test_add"]


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_an_agents_work_reaches_the_grader_only_as_its_patch(tmp_path, monkeypatch):
    from tests.core.test_staged import FakeEnv, _sandbox
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (task,) = load_bank(_bank(tmp_path, _row()), preset="swebench")
    assert task.grader.env == task.workspace_env and task.grader.submission

    class Repo(FakeEnv):                     # the image's /testbed is a git checkout
        def fresh(self):
            m = super().fresh()
            git = "git -c user.name=t -c user.email=t@t"
            m.exec(f"git init -q . && {git} add -A && {git} commit -qm base", cwd="/testbed")
            return m
    env = Repo(task.workspace_env, {"/testbed/calc.py": BUGGY})
    with _sandbox(task, {"ws": env, "grader": env}) as sb:
        sb.build()
        # uncommitted edits count (the harness diffs everything against the base commit);
        # what the agent leaves outside the repo never reaches the fresh grading container
        fix = "sed -i.bak 's/a - b/a + b/' calc.py && rm calc.py.bak && touch /tmp/left-behind"
        with sb.agent_overlay(fix) as ovl:
            assert run_grader(ovl, task).passed
            assert ovl.exec("test -e /tmp/left-behind").exit_code != 0
