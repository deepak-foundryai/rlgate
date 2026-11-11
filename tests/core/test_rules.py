"""The rule catalogue covers every probe, and every emitted finding carries a valid rule ID."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rlgate.cli import main
from rlgate.core import rules
from rlgate.core.registry import discover
from rlgate.ingest import spec
from rlgate.pipeline import audit


def test_rule_ids_are_well_formed_and_grouped():
    for rid, r in rules.RULES.items():
        assert re.fullmatch(r"RG\d{3}", rid), rid
        assert r.question.value == rid[:3]
        assert r.title and r.why and r.fix, rid


def test_every_probe_has_a_rule_and_every_rule_has_a_probe():
    probes = set(discover())
    owners = {r.probe_id for r in rules.RULES.values()}
    assert probes <= owners, f"probes without a rule: {sorted(probes - owners)}"
    built_in = {"baseline.invariant", "baseline.build"}     # the step 1 and 2 gates
    assert owners - probes <= built_in, sorted(owners - probes - built_in)


def test_reward_codes_map_to_rg5xx():
    assert rules.REWARD_CODE_RULES == {f"A{i}": f"RG50{i}" for i in range(1, 8)}
    for rid in rules.REWARD_CODE_RULES.values():
        assert rules.get(rid).probe_id == "reward_shape.conformance"


def test_multi_rule_probes_have_no_default():
    assert rules.default_rule("leakage.cache_scan") is None
    assert rules.default_rule("grader_strength.revert_probe") == "RG101"


def test_unknown_rule_raises_clear_error():
    with pytest.raises(KeyError, match="rlgate/core/rules.py"):
        rules.get("RG999")


def _fixture_tasks() -> list[Path]:
    return sorted(p.parent for p in Path("tests/fixtures").rglob("task.rlgate.yaml"))


@pytest.mark.parametrize("fixture", _fixture_tasks(), ids=lambda p: p.name)
def test_static_findings_all_carry_valid_rule_ids(fixture):
    result = audit(spec.load(fixture), static_only=True, include_review=True)
    for f in result.findings:
        assert f.rule_id in rules.RULES, (f.probe_id, f.title)
        assert rules.get(f.rule_id).probe_id == f.probe_id


def test_live_findings_all_carry_valid_rule_ids():
    result = audit(spec.load("tests/fixtures/grader_strength/weak_suite"), k=1,
                   backend="local", include_review=True)
    assert result.findings
    for f in result.findings:
        assert f.rule_id in rules.RULES, (f.probe_id, f.title)


def test_rules_command_lists_the_six_steps(capsys):
    assert main(["rules"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    for n, name in enumerate(["Build", "Baseline", "Leaks", "Grader", "Cheating",
                              "Fairness"], 1):
        assert f"Step {n} {name}" in out
    assert "Does the grader really check the fix?" in out and "RG101" in out
    assert "not checked yet" in out          # the optional difficulty / duplicate checks


def test_every_rule_belongs_to_one_step_in_its_range():
    for rid, r in rules.RULES.items():
        if rid[:3] in ("RG8", "RG9"):
            assert r.step is None
            continue
        assert r.step is not None and rid[:3] in r.step.ranges, rid


def test_every_rule_has_explain_docs_and_the_catalogue_page_is_current():
    from pathlib import Path

    from rlgate.core import rules
    from rlgate.core.rule_docs import DOCS, catalogue_markdown

    assert set(DOCS) == set(rules.RULES)
    page = Path(__file__).resolve().parents[2] / "docs" / "rules.md"
    assert page.read_text() == catalogue_markdown(), \
        "docs/rules.md is stale: run python -m rlgate.core.rule_docs"
