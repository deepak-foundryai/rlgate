"""Every proven, execution-based finding carries a working `rlgate repro` command."""

from __future__ import annotations

import shlex
from pathlib import Path

from rlgate.cli import main
from rlgate.core import repro
from rlgate.ingest import spec
from rlgate.pipeline import audit

WEAK = "tests/fixtures/grader_strength/weak_suite"
CONFTEST = "tests/fixtures/forgery/conftest_hijack"


def _run_replay(cmd: str, capsys) -> tuple[int, str]:
    argv = shlex.split(cmd)
    assert argv[:2] == ["rlgate", "repro"], cmd
    code = main(argv[1:] + ["--backend", "local"])
    return code, capsys.readouterr().out


def test_revert_finding_reproduces(capsys):
    result = audit(spec.load(WEAK), k=1, backend="local",
                   only={"grader_strength.revert_probe"})
    [f] = [f for f in result.findings if f.rule_id == "RG101"]
    assert f.evidence.replay and f.evidence.replay.startswith("rlgate repro RG101 ")

    code, out = _run_replay(f.evidence.replay, capsys)
    assert code == 0, out
    assert "reproduced" in out and "not reproduced" not in out


def test_conftest_forgery_reproduces(capsys):
    result = audit(spec.load(CONFTEST), k=1, backend="local",
                   only={"forgery.conftest_injection"})
    [f] = [f for f in result.findings if f.rule_id == "RG201"]
    code, out = _run_replay(f.evidence.replay, capsys)
    assert code == 0, out
    assert "conftest.py" in out


def test_proof_ids_are_deterministic():
    task = spec.load(WEAK)
    a = audit(task, k=1, backend="local", only={"grader_strength.revert_probe"})
    b = audit(task, k=1, backend="local", only={"grader_strength.revert_probe"})
    assert [f.evidence.replay for f in a.findings] == [f.evidence.replay for f in b.findings]
    assert len(repro.find(task.id, "RG101")) == 1


def _trivial_task(tmp_path: Path) -> Path:
    (tmp_path / "repo").mkdir()
    (tmp_path / "task.rlgate.yaml").write_text(
        "id: always_pass\nworkspace: ./repo\ngrader:\n  run: \"true\"\n")
    return tmp_path


def test_do_nothing_pass_is_rg002_and_reproduces(tmp_path, capsys):
    target = _trivial_task(tmp_path)
    result = audit(spec.load(target), k=1, backend="local")
    assert not result.baseline_sane
    [f] = [f for f in result.findings if f.rule_id == "RG002"]
    code, out = _run_replay(f.evidence.replay, capsys)
    assert code == 0 and "reproduced" in out


def test_repro_without_a_recorded_proof_explains_next_step(tmp_path, capsys):
    target = _trivial_task(tmp_path)
    assert main(["repro", "RG101", str(target), "--backend", "local"]) == 2
    err = capsys.readouterr().err
    assert "rlgate check" in err


def test_repro_unknown_rule(capsys):
    assert main(["repro", "RG999", WEAK]) == 2
    assert "rlgate explain" in capsys.readouterr().err
