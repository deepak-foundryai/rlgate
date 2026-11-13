"""The static report (no --deep-review) holds only proven exploits and concrete facts.

The weak-test checks (rules.DEEP_ONLY) run and report only with --deep-review; settings a
format doesn't state are not reported as unsafe; and a reference that fails only because of
the sandbox doesn't stop the run as a broken task."""

from __future__ import annotations

from pathlib import Path

from rlgate.baseline import invariant
from rlgate.core import rules
from rlgate.core.context import Baseline
from rlgate.core.finding import VerdictTier
from rlgate.core.task import EnvSpec, GraderSpec, Task
from rlgate.ingest import harbor, spec
from rlgate.pipeline import CheckRun, audit
from rlgate.probes.leakage.open_internet import OpenInternet
from tests.core.test_harbor_loader import _task

WEAK = "tests/fixtures/grader_strength/weak_suite"


# -- weak-test checks run only with --deep-review ----------------------------------------

def test_a_static_run_skips_the_weak_test_checks(monkeypatch):
    monkeypatch.delenv("RLGATE_WEAK_TESTS", raising=False)
    result = audit(spec.load(WEAK), k=1, backend="local", include_review=True)
    assert not {f.rule_id for f in result.findings} & rules.DEEP_ONLY
    revert = [c for c in result.checks if c.probe_id == "grader_strength.revert_probe"]
    assert revert and revert[0].status == CheckRun.SKIPPED_DEEP


def test_the_same_task_shows_them_when_asked(monkeypatch):
    monkeypatch.delenv("RLGATE_WEAK_TESTS", raising=False)
    result = audit(spec.load(WEAK), k=1, backend="local", include_review=True,
                   weak_tests=True, only={"grader_strength.revert_probe"})
    assert "RG101" in {f.rule_id for f in result.findings}


def test_deep_only_rules_name_real_rules_and_whole_probes_skip():
    assert rules.DEEP_ONLY <= set(rules.RULES)
    assert rules.probe_deep_only("grader_strength.mutation_probe")
    assert not rules.probe_deep_only("baseline.invariant")      # RG001 etc. stay static
    assert not rules.probe_deep_only("forgery.conftest_injection")


# -- the agent's network: reported only when the task says it's open ----------------------

def _agent_network(tmp_path: Path, toml: str) -> bool | None:
    t = harbor.load(_task(tmp_path, toml=toml, env_df="FROM python:3.12\nWORKDIR /app\n"))
    assert t.workspace_env is not None
    return t.workspace_env.agent_network


def test_agent_network_mode_with_a_hyphen_turns_it_off(tmp_path):
    assert _agent_network(tmp_path, '[agent]\nnetwork_mode = "no-network"\n') is False


def test_allow_internet_false_in_environment_turns_it_off(tmp_path):
    assert _agent_network(tmp_path, "[environment]\nallow_internet = false\n") is False


def test_harbor_default_is_open(tmp_path):
    assert _agent_network(tmp_path, "") is True


def test_the_verifiers_network_setting_is_not_the_agents(tmp_path):
    assert _agent_network(tmp_path, '[verifier]\nnetwork_mode = "no-network"\n') is True


def test_a_format_that_does_not_say_gets_no_open_internet_finding():
    task = Task(id="t", source="swe", repo_dir=Path("."), grader=GraderSpec(run="grade"),
                workspace_env=EnvSpec(image="img"))         # agent_network: None
    assert OpenInternet().run(task, None) == []             # type: ignore[arg-type]


def test_a_gpu_task_is_marked_not_fully_checkable(tmp_path):
    t = harbor.load(_task(tmp_path, toml="[environment]\ngpus = 1\n",
                          env_df="FROM python:3.12\n"))
    assert any("GPU" in u for u in t.meta.get("unsupported", []))


# -- the reference fails only where the sandbox does ------------------------------------

def _swe_task() -> Task:
    return Task(id="t", source="swe", repo_dir=Path("."),
                grader=GraderSpec(run="grade", verdict="tests", fail_to_pass=("t_fix",),
                                  pass_to_pass=("t_old", "t_net")))


def _baseline(gold: dict[str, bool], nop: dict[str, bool]) -> Baseline:
    return Baseline(oracle=0.0, nop=0.0, k=2, per_test=gold, nop_per_test=nop,
                    reports=True, oracle_runs=(0.0, 0.0), nop_runs=(0.0, 0.0))


def test_tests_that_fail_with_and_without_the_fix_are_the_sandbox(monkeypatch):
    b = _baseline({"t_fix": True, "t_old": True, "t_net": False},
                  {"t_fix": False, "t_old": True, "t_net": False})
    [f] = [f for f in invariant.findings(_swe_task(), b) if f.rule_id == "RG001"]
    assert f.verdict is VerdictTier.REVIEW                  # shown, doesn't stop the run
    assert "t_net" in f.evidence.detail and "without the fix too" in f.evidence.detail


def test_a_test_the_fix_should_make_pass_is_still_a_broken_reference():
    b = _baseline({"t_fix": False, "t_old": True, "t_net": True},
                  {"t_fix": False, "t_old": True, "t_net": True})
    [f] = [f for f in invariant.findings(_swe_task(), b) if f.rule_id == "RG001"]
    assert f.verdict is VerdictTier.CONFIRMED


def test_a_reference_grading_with_no_verdict_runs_again(monkeypatch):
    calls: list[bool] = []

    def once(task, sandbox, gold, fresh):
        calls.append(fresh)
        silent = len(calls) == 1                     # the first grading wrote no reward
        return (0.0 if silent else 1.0), None, 1.0, silent

    monkeypatch.setattr(invariant, "_run_once", once)
    reward, _, _ = invariant._run(None, None, gold=True)    # type: ignore[arg-type]
    assert reward == 1.0 and calls == [False, True]


def test_do_nothing_gradings_are_not_retried(monkeypatch):
    calls: list[bool] = []

    def once(task, sandbox, gold, fresh):
        calls.append(fresh)
        return 0.0, None, 1.0, True

    monkeypatch.setattr(invariant, "_run_once", once)
    invariant._run(None, None, gold=False)                  # type: ignore[arg-type]
    assert calls == [False]
