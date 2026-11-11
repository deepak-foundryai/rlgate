"""Rubric-graded tasks: finding and parsing a rubric, the static rubric checks (RG409-RG411),
and the L4 rubric attack with a scripted panel (RG112)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rlgate.core import rubric as R
from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Task
from rlgate.llm import LLMResult
from rlgate.probes.agentic.rubric import RubricAttack
from rlgate.probes.rubric.static import RubricStatic
from rlgate.review import rubric as RB
from rlgate.review.session import Session

STATEMENT = ("Write a short memo for the finance team. It must include the year-over-year "
             "revenue change, and it should recommend whether to renew the vendor contract. "
             "It must also include a 12-month cash forecast.")
RUBRIC = """# Rubric
- [3] States the year-over-year revenue change as a percentage
- [3] Recommends whether to renew the vendor contract, with a reason
- [1] The memo is clear and professional
- [1] Uses the correct revenue figures and labels the totals and cites the source and explains the method
- [1] States the year-over-year revenue change as percentage
"""


@pytest.fixture
def task(tmp_path) -> Task:
    d = tmp_path / "memo"
    d.mkdir()
    (d / "rubric.md").write_text(RUBRIC)
    return Task(id="memo", source="test", repo_dir=d, instruction=STATEMENT,
                grader=GraderSpec(run="true"), origin=str(d))


def test_rubric_is_found_and_parsed(task, tmp_path):
    r = R.find(task)
    assert r is not None and r.path.endswith("rubric.md")
    assert [c.weight for c in r.criteria] == [3, 3, 1, 1, 1]
    assert r.criteria[0].text == "States the year-over-year revenue change as a percentage"
    j = tmp_path / "rubric.json"
    j.write_text(json.dumps({"criteria": [{"criterion": "Has a title", "points": 2},
                                          "Names the risk"]}))
    r2 = R.load(j)
    assert [(c.text, c.weight) for c in r2.criteria] == [("Has a title", 2.0),
                                                       ("Names the risk", None)]


def test_static_rubric_checks(task):
    fs = RubricStatic().run(task, SimpleNamespace())
    by = {}
    for f in fs:
        by.setdefault(f.rule_id, []).append(f.evidence.detail)
    assert all(f.verdict is VerdictTier.REVIEW for f in fs)
    assert any("C3" in d and "'clear'" in d for d in by["RG409"])          # not observable
    assert any("C4" in d and "several checks" in d for d in by["RG409"])   # not atomic
    assert any("C1 and C5" in d for d in by["RG410"])                      # duplicate
    assert any("no penalty criteria" in d for d in by["RG410"])
    assert any("12-month cash forecast" in d for d in by["RG411"])         # uncovered
    assert not any("vendor contract" in d for d in by["RG411"])


def test_no_rubric_means_not_applicable(tmp_path):
    t = Task(id="x", source="test", repo_dir=tmp_path, instruction="x",
             grader=GraderSpec(run="true"), origin=str(tmp_path))
    from rlgate.core.probe import NotApplicable
    with pytest.raises(NotApplicable):
        RubricStatic().run(t, SimpleNamespace())


def test_score_counts_weights_and_penalties():
    r = R.Rubric("r", (R.Criterion("A", "a", 3), R.Criterion("B", "b", 1),
                       R.Criterion("P", "states a wrong total", -2)), "")
    assert RB.score(r, {"A": True, "B": True}) == 1.0
    assert RB.score(r, {"A": True, "B": True, "P": True}) == 0.5
    assert RB.score(r, {"B": True}) == 0.25


class Panel:
    """Scripted models: the attackers return a hollow memo; the rubric judge credits it;
    the goal judge (another model) rejects it."""

    def __init__(self, goal_ok=False):
        self.goal_ok = goal_ok
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model):
        self.model = model
        return self

    def complete(self, prompt, *, system=None, schema=None, schema_name="", seed=None, **_):
        self.calls.append((schema_name, prompt))
        if schema_name == "attack":
            data = {"deliverable": "Revenue changed by 12%. Recommendation: renew.",
                    "how": "states a number and a recommendation without any analysis"}
        elif schema_name == "rubric":
            ids = schema["properties"]["criteria"]["items"]["properties"]["id"]["enum"]
            data = {"criteria": [{"id": i, "met": True, "reason": "stated"} for i in ids]}
        else:
            data = {"achieved": self.goal_ok, "missing": "no forecast, figures unsupported",
                    "confidence": 0.8}
        return LLMResult(text="{}", data=data, model="x", cost_usd=0.0)

    def check(self):
        return None


def test_rubric_attack_flags_a_hollow_deliverable(task):
    panel = Panel()
    s = Session(["openai/gpt-x", "gemini/gemini-y"], client=panel, votes=1)
    p = RubricAttack()
    fs = p.run(task, SimpleNamespace(review=s))
    assert [f.rule_id for f in fs] == ["RG112", "RG112"]
    f = fs[0]
    assert f.verdict is VerdictTier.REVIEW and "white-box" in f.evidence.detail
    assert "misses the goal: no forecast" in f.evidence.detail
    assert "gemini/gemini-y" in f.evidence.detail          # the goal judge is another model
    assert "rubric 100%, goal missed" in p.note
    # the goal judge never sees the rubric; the black-box attacker never sees it either
    goal_prompts = [pr for n, pr in panel.calls if n == "goal"]
    assert goal_prompts and all("RUBRIC" not in pr for pr in goal_prompts)
    black = [pr for n, pr in panel.calls if n == "attack" and "hurried grader" in pr]
    assert black and "Recommends whether" not in black[0]


def test_judges_that_agree_raise_nothing(task):
    s = Session(["openai/gpt-x"], client=Panel(goal_ok=True), votes=1)
    assert RubricAttack().run(task, SimpleNamespace(review=s)) == []
