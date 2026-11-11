"""L1 grader probes, end to end on local fixtures (host backend: no Docker).

Each fixture has a real hole the probe must prove by running the grader, and the clean example
(held-out grader, type-pinned, reward file) must survive every one of them.
"""

from __future__ import annotations

# ruff: noqa: S102 - the generators' output is exec'd to prove it is valid, working Python
import ast

import pytest

from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Task
from rlgate.ingest import spec
from rlgate.pipeline import CheckRun, audit
from rlgate.probes.forgery.report_forgery import fake_report
from rlgate.probes.forgery.test_selection import ini_with
from rlgate.probes.forgery.verifier_command import VerifierCommand
from rlgate.sandbox.parsers import TestStatus, parse_report
from rlgate.variants import python as py
from rlgate.variants import visible
from rlgate.variants.source import FileChange, changed_functions

WEAK = "tests/fixtures/grader_strength/weak_suite"
SHARED = "tests/fixtures/grader_strength/shared_hunk"   # f and g fixed in ONE hunk
PYTEST_TASK = "tests/fixtures/forgery/conftest_hijack"
SLOW = "tests/fixtures/forgery/slow_task"
REPORT = "tests/fixtures/forgery/forged_report"
CLEAN = "examples/taskbank/clean"


def _rules(path: str, *probes: str) -> dict[str, list]:
    r = audit(spec.load(path), k=1, only=set(probes), include_review=True)
    out: dict[str, list] = {}
    for f in r.findings:
        out.setdefault(f.rule_id, []).append(f)
    return out


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "state"))


# -- coverage probes (step 4) ------------------------------------------------------------

def test_stub_probe_finds_what_revert_cannot():
    # one hunk fixes f and g; f is checked, so removing the hunk fails, but stubbing g passes
    assert "RG101" not in _rules(SHARED, "grader_strength.revert_probe")
    fs = _rules(SHARED, "grader_strength.stub_probe")["RG102"]
    assert [f.title.rsplit(": ", 1)[-1] for f in fs] == ["g"]       # f is checked; g isn't
    assert fs[0].verdict is VerdictTier.CONFIRMED
    assert fs[0].evidence.replay.startswith("rlgate repro RG102")


def test_stub_probe_skips_functions_already_proven_untested():
    # in weak_suite g's own hunk is removable (RG101), so stubbing g would only repeat it
    assert "RG102" not in _rules(WEAK, "grader_strength.stub_probe")


def test_hardcode_probe_proves_visible_answers_are_enough():
    fs = _rules(WEAK, "grader_strength.hardcode_probe")["RG104"]
    assert len(fs) == 1 and fs[0].verdict is VerdictTier.CONFIRMED
    assert "f replaced by a lookup table of the 1 input(s)" in fs[0].evidence.detail
    assert "no held-out tests" in fs[0].evidence.detail


def test_mutation_probe_negates_and_drops_branches():
    from rlgate.core.task import Patch
    from rlgate.variants.mutate import mutate_variants
    ops = {v.operator for v in mutate_variants(Patch(
        "--- a/m.py\n+++ b/m.py\n@@ -1,0 +1,2 @@\n+if n > 1:\n+    return n\n"))}
    assert {"negate_condition", "drop_branch", "decrement", "increment"} <= ops


# -- verdict-channel probes (step 5) -----------------------------------------------------

def test_exit_code_forgery_on_an_exit_code_grader():
    fs = _rules(WEAK, "forgery.exit_code")["RG202"]
    assert fs and all(f.verdict is VerdictTier.CONFIRMED for f in fs)
    assert "calc.py" in fs[0].evidence.detail


def test_exit_code_forgery_fools_pytest_through_an_import():
    assert "RG202" in _rules(PYTEST_TASK, "forgery.exit_code")


def test_equality_override_fools_double_equals():
    fs = _rules(PYTEST_TASK, "forgery.equality_override")["RG204"]
    assert fs[0].location.startswith("mod.py")


def test_test_selection_changes_are_proven():
    fs = _rules(PYTEST_TASK, "forgery.test_selection")["RG207"]
    names = {f.evidence.detail.rsplit("Payload: ", 1)[-1].rstrip(".") for f in fs}
    assert {"collect-only", "testpaths", "mark-skip", "mark-xfail"} <= names


def test_rewriting_the_visible_tests_is_proven():
    fs = _rules(PYTEST_TASK, "forgery.test_rewrite")["RG214"]
    assert fs[0].location == "test_feature.py"


def test_conftest_injection_still_proven():
    assert "RG201" in _rules(PYTEST_TASK, "forgery.conftest_injection")


def test_timer_tampering_on_a_timed_task():
    fs = _rules(SLOW, "forgery.timer_tampering")["RG206"]
    assert any("Payload: clock-in-code" in f.evidence.detail for f in fs)


def test_report_forgery_on_a_grader_that_trusts_an_existing_report():
    fs = _rules(REPORT, "forgery.report_forgery")["RG203"]
    assert fs[0].location == "results.xml"


def test_env_hijack_says_it_needs_an_isolated_backend_on_the_host():
    r = audit(spec.load(PYTEST_TASK), k=1, only={"forgery.env_hijack"}, include_review=True)
    c = next(c for c in r.checks if c.probe_id == "forgery.env_hijack")
    assert c.status == CheckRun.NA and "docker or daytona" in c.reason


def test_clean_example_survives_every_l1_probe():
    l1 = {"grader_strength.revert_probe", "grader_strength.mutation_probe",
          "grader_strength.stub_probe", "grader_strength.hardcode_probe",
          "forgery.conftest_injection", "forgery.exit_code", "forgery.report_forgery",
          "forgery.equality_override", "forgery.test_selection", "forgery.test_rewrite",
          "forgery.timer_tampering", "forgery.verifier_command"}
    r = audit(spec.load(CLEAN), k=1, only=l1, include_review=True)
    assert r.findings == []
    ran = {c.probe_id for c in r.checks if c.status == CheckRun.GOOD}
    assert {"grader_strength.stub_probe", "forgery.exit_code", "forgery.equality_override",
            "forgery.report_forgery"} <= ran


# -- verifier command (file-only) ---------------------------------------------------------

def _vc(tmp_path, script: str, verdict: str = "reward_file"):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(script)
    task = Task(id="t", source="x", repo_dir=tmp_path / "ws",
                grader=GraderSpec(run="bash /tests/test.sh", verdict=verdict,
                                  inject=((str(tests), "/tests"),)))
    (tmp_path / "ws").mkdir()
    return [f.evidence.detail for f in VerifierCommand().run(task, None)]  # type: ignore[arg-type]


def test_verifier_command_flags_ignored_failures_and_steered_selection(tmp_path):
    out = _vc(tmp_path, "#!/bin/bash\npytest /tests --lf || true\n"
                        "if [ $? -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; fi\n")
    assert any("always 0" in d for d in out)
    assert any("--lf" in d for d in out)


def test_verifier_command_flags_unconditional_reward_and_superficial_checks(tmp_path):
    out = _vc(tmp_path, "#!/bin/bash\n[ -f /app/out.txt ]\necho 1 > /logs/verifier/reward.txt\n")
    assert any("outside any condition" in d for d in out)
    assert any("runs no test or program" in d for d in out)


def test_verifier_command_is_quiet_on_a_sound_script(tmp_path):
    assert _vc(tmp_path, "#!/bin/bash\nif pytest /tests; then\n  echo 1 > /logs/verifier/"
                         "reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n"
                         "find /x -name 'pytest.ini' -delete || true\n"
                         "pytest /tests; s=$?\n"
                         "if [ $s -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; "
                         "else echo 0 > /logs/verifier/reward.txt; fi\n") == []


# -- generators -----------------------------------------------------------------------

SRC = '''"""doc"""
from __future__ import annotations


def add(a, b):
    """Add."""
    return a + b


class Calc:
    def mul(self, a, b):
        return a * b
'''


def _spans():
    fc = FileChange("m.py", SRC, SRC, frozenset({7, 12}))
    return fc, changed_functions(fc)


def test_changed_functions_traits():
    _, spans = _spans()
    assert [(s.qualname, s.is_method, s.returns_value) for s in spans] == \
        [("add", False, True), ("Calc.mul", True, True)]


def test_prelude_goes_after_docstring_and_future_imports():
    out = py.prelude(SRC, ["X = 1"])
    lines = out.splitlines()
    assert lines[1] == "from __future__ import annotations" and lines[2] == "X = 1"
    ast.parse(out)


def test_stub_always_equal_and_hardcode_produce_valid_python():
    _, spans = _spans()
    for s in spans:
        ast.parse(py.stub(SRC, s, ["return None"]))
        ns: dict = {}
        exec(py.always_equal(SRC, s), ns)
    ns = {}
    exec(py.hardcode(SRC, [(spans[0], {repr(((1, 2), [])): "3"})]), ns)
    assert ns["add"](1, 2) == 3 and ns["add"](5, 5) is None


def test_trace_round_trip(tmp_path):
    _, spans = _spans()
    log = tmp_path / "t.jsonl"
    ns: dict = {}
    exec(py.trace_appendix(SRC, spans, str(log)), ns)
    ns["add"](1, 2)
    ns["Calc"]().mul(2, 3)
    t = py.parse_trace(log.read_text())
    assert t == {"add": {repr(((1, 2), [])): "3"}, "Calc.mul": {repr(((2, 3), [])): "6"}}


def test_blank_tests_empties_test_bodies_only():
    src = "def helper():\n    return 1\n\n\ndef test_a():\n    assert helper() == 2\n\n\n" \
          "class TestB:\n    def test_b(self):\n        assert False\n"
    out = py.blank_tests(src)
    assert "assert" not in out and "return 1" in out
    ns: dict = {}
    exec(out, ns)
    ns["test_a"]()
    ns["TestB"]().test_b()


def test_test_file_maps_report_ids_back_to_the_workspace(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    assert visible.test_file("tests/test_x.py::TestA::test_b", tmp_path) == "tests/test_x.py"
    assert visible.test_file("/app/tests/test_x.py::test_b", tmp_path) == "tests/test_x.py"
    assert visible.test_file("tests.test_x.TestA.test_b", tmp_path) == "tests/test_x.py"
    assert visible.test_file("/tests/test_outputs.py::test_y", tmp_path) is None


@pytest.mark.parametrize("fmt", ["junit", "ctrf", "vitest_json", "go_json", "trx"])
def test_fake_reports_parse_as_all_passed(fmt):
    rep = parse_report(fmt, fake_report(fmt, ["tests/test_x.py::test_a", "test_b"]) or "")
    assert rep.cases and all(c.status is TestStatus.PASS for c in rep.cases)


def test_ini_with_keeps_existing_options():
    out = ini_with("[pytest]\nmarkers = slow\n", addopts="--collect-only -q")
    assert "markers = slow" in out and "addopts = --collect-only -q" in out
