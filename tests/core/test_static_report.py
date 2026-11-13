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


def test_a_step_left_to_deep_review_says_so(monkeypatch):
    from rlgate.core.questions import outcome
    monkeypatch.delenv("RLGATE_WEAK_TESTS", raising=False)
    result = audit(spec.load(WEAK), k=1, backend="local", include_review=True)
    [grader] = [r for r in outcome(result, quick=False).rows if r.step.label == "Grader"]
    assert grader.summary == "runs with --deep-review"


# -- RG204 needs equality to be what passes ---------------------------------------------

def _equality_task(tmp_path: Path, test_body: str) -> Path:
    """A fix that changes ``feature()`` and adds a module flag; the test is given."""
    d = tmp_path / "task"
    (d / "repo").mkdir(parents=True)
    (d / "repo" / "mod.py").write_text("def feature():\n    return 0\n")
    (d / "repo" / "test_feature.py").write_text(test_body)
    (d / "fix.patch").write_text("--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,4 @@\n"
                                 "+FLAG = True\n+\n def feature():\n-    return 0\n"
                                 "+    return 42\n")
    (d / "task.rlgate.yaml").write_text(
        "id: eq\nsource: fixture\nworkspace: ./repo\ngold_patch: ./fix.patch\n"
        "grader:\n  run: \"python3 -m pytest -q -p no:cacheprovider\"\n"
        "languages: [python]\n")
    return d


def _equality_rules(d: Path) -> set[str]:
    r = audit(spec.load(str(d)), k=1, backend="local", include_review=True,
              only={"forgery.equality_override"})
    return {f.rule_id for f in r.findings}


def test_an_always_equal_object_that_tests_compare_is_reported(tmp_path):
    d = _equality_task(tmp_path, "import mod\n\n\ndef test_it():\n"
                                 "    assert mod.FLAG and mod.feature() == 42\n")
    assert "RG204" in _equality_rules(d)


def test_a_result_the_tests_throw_away_is_not_an_equality_hole(tmp_path, monkeypatch):
    # feature() is called but its result is never compared: an object equal to nothing
    # passes too, so equality isn't what lets the change through
    d = _equality_task(tmp_path, "import mod\n\n\ndef test_it():\n"
                                 "    mod.feature()\n    assert mod.FLAG\n")
    assert "RG204" not in _equality_rules(d)
    # without the control, the old check reported it
    from rlgate.probes.forgery import equality_override as eo
    real = eo.EqualityOverride._passes
    monkeypatch.setattr(eo.EqualityOverride, "_passes", lambda self, ctx, v: (
        (False, 0.0) if v.operator == "never_equal" else real(self, ctx, v)))
    assert "RG204" in _equality_rules(d)


# -- RG607: installing the project itself is local ----------------------------------------

def test_pip_installing_the_project_itself_is_local(tmp_path):
    from rlgate.probes.hygiene.grader_downloads import _local_pip
    t = Task(id="t", source="f", repo_dir=tmp_path, grader=GraderSpec(run="x"))
    assert _local_pip("python -m pip install -e .", t) == "local"
    assert _local_pip("pip install --no-build-isolation .", t) == "local"
    assert _local_pip("pip install numpy", t) is None
    assert _local_pip("pip install -r requirements.txt", t) is None
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires = ["setuptools"]\n')
    assert "build tools" in (_local_pip("python -m pip install -e .", t) or "")
    assert _local_pip("pip install --no-build-isolation -e .", t) == "local"


def test_a_reference_that_passes_on_some_runs_is_flaky_not_broken():
    b = Baseline(oracle=0.0, nop=0.0, k=5, oracle_runs=(1.0, 0.0, 1.0, 1.0, 0.0),
                 nop_runs=(0.0,) * 5, flaky_checks=frozenset({"oracle"}))
    task = Task(id="t", source="f", repo_dir=Path("."), grader=GraderSpec(run="grade"))
    got = {f.rule_id for f in invariant.findings(task, b)}
    assert "RG003" in got and "RG001" not in got


# -- RG201: held-out tests that import what the fix adds still collect -------------------

def test_the_planted_conftest_gets_tests_that_import_new_names_to_pass(tmp_path):
    import subprocess
    import sys

    from rlgate.probes.forgery.conftest_injection import _HOOK, _STUBS
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("def old():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text(
        "import pkg\nfrom pkg import old, NewThing\nfrom pkg.newmod import helper\n\n\n"
        "def annotated(store: pkg.Store) -> pkg.Store:\n    return store\n\n\n"
        "class TestNew(NewThing):\n    pass\n\n\n"
        "def test_it():\n    assert NewThing(1).value == 1 and helper() == 3 and old() == 2\n")

    def pytest() -> int:
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "tests"], cwd=tmp_path, capture_output=True, check=False).returncode

    assert pytest() != 0                                    # base: collection error
    (tmp_path / "conftest.py").write_text("\n".join(_HOOK + ["", ""] + _STUBS) + "\n")
    assert pytest() == 0
