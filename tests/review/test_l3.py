"""L3 agentic audits, end to end on a local fixture with a scripted agent (no network): the
agent's tools, both judges' proof rules, solver-visible limits, the L2 hand-off, and
fail-closed behaviour. Grading is real: the host backend runs the fixture's pytest verifier.

tests/fixtures/agentic/shop: the statement asks for classify() and double(); the held-out
tests also require a print the statement never mentions (too strict), and check double() on
one input only (too weak).
"""

from __future__ import annotations

import json

import pytest

from rlgate.core.finding import VerdictTier
from rlgate.ingest import spec
from rlgate.llm import ChatTurn, LLMResult
from rlgate.pipeline import CheckRun, audit
from rlgate.review import judges as J
from rlgate.review.session import Session

SHOP = "tests/fixtures/agentic/shop"
CLASSIFY_Q = "returns 'big' when n > 10 and 'small' otherwise"
DOUBLE_Q = "returns x multiplied by 2 for any integer x"
GOOD_CLASSIFY = ('def classify(n):\n    print("classify called")\n    if n > 10:\n'
                 '        return "big"\n    return "small"\n')
WRONG_DOUBLE = GOOD_CLASSIFY + "\n\ndef double(x):\n    return x + 2\n"
QUIET = ('def classify(n):\n    return "big" if n > 10 else "small"\n\n\n'
         'def double(x):\n    return x * 2\n')
CHECK_DOUBLE = 'python3 -c "import shop; assert shop.double(3) == 6"'
CHECK_CLASSIFY = ("python3 -c \"import shop; assert shop.classify(11) == 'big' and "
                  "shop.classify(1) == 'small'\"")

REQS = [{"id": "R1", "text": "classify big/small", "quote": CLASSIFY_Q, "source": "statement"},
        {"id": "R2", "text": "double any integer", "quote": DOUBLE_Q, "source": "statement"}]
STRENGTH = [
    [("record_requirements", {"requirements": REQS})],
    [("write_file", {"path": "shop.py", "content": WRONG_DOUBLE})],
    [("try_bypass", {"requirement_id": "R2", "how": "adds 2 instead of doubling",
                     "check_cmd": CHECK_DOUBLE})],
    [("finish", {"summary": "double is checked on one input"})],
]
AMBIGUITY = [
    [("write_file", {"path": "shop.py", "content": QUIET})],
    [("try_alternative", {"requirement_quote": CLASSIFY_Q, "test": "test_logs",
                          "assertion_quote": 'assert "classify called" in buf.getvalue()',
                          "why_valid": "classifies correctly without printing anything",
                          "check_cmd": CHECK_CLASSIFY, "expected_failures": ["test_logs"]})],
    [("finish", {"summary": "the print is never asked for"})],
]
L3 = {"agentic.strength", "agentic.ambiguity"}


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "state"))


class Agent:
    """A scripted model: ``chat`` plays the script for the audit it is in (told apart by the
    prompt) and records every tool result; ``complete`` answers L2 calls (clean unless
    ``l2`` says otherwise)."""

    def __init__(self, strength=None, ambiguity=None, l2: dict | None = None) -> None:
        self.scripts = {"strength": list(strength or [[("finish", {})]]),
                        "ambiguity": list(ambiguity or [[("finish", {})]])}
        self.l2 = l2 or {}
        self.results: dict[str, list[str]] = {"strength": [], "ambiguity": []}
        self.prompts: dict[str, str] = {}
        self.tools: dict[str, list[str]] = {}

    def __call__(self, model: str):
        return self

    def chat(self, messages, tools):
        kind = "strength" if "too WEAK" in messages[1]["content"] else "ambiguity"
        self.prompts[kind] = messages[1]["content"]
        self.tools[kind] = [t["function"]["name"] for t in tools]
        self.results[kind] += [m["content"] for m in messages if m.get("role") == "tool"
                               and m["content"] not in self.results[kind]]
        script = self.scripts[kind]
        step = script.pop(0) if script else []
        calls = [(f"c{len(messages)}_{i}", name, json.dumps(args))
                 for i, (name, args) in enumerate(step)]
        msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c, "type": "function", "function": {"name": n, "arguments": a}}
            for c, n, a in calls]}
        return ChatTurn(msg, calls, cost_usd=0.01)

    def complete(self, prompt, *, system=None, schema=None, schema_name="result", seed=None,
                 **_):
        schema = schema or {}
        if "results" in schema["properties"]:
            names = schema["properties"]["results"]["items"]["properties"]["criterion"]["enum"]
            art = next(a for a, cs in J.FIRST.items() if [c.name for c in cs] == names)
            data = {"results": [{"criterion": c.name, "verdict": "pass", "test": "",
                                 "quote": "", "quote_from": "", "gap": "", "confidence": 0.9,
                                 "fix": ""} for c in J.FIRST[art]]}
        elif schema_name == "rewrite":
            data = {"statement": "rewritten\n"}
        else:
            q = next(q for q, c in J.SECOND.items() if c.text in prompt)
            data = self.l2.get(q, {"verdict": "ok", "negatives": []})
        return LLMResult(text="{}", data=data, model="openai/gpt-x", cost_usd=0.0)

    def check(self):
        return None


def _run(agent: Agent, only=L3, **kw):
    s = Session(["openai/gpt-x"], client=agent, votes=1, **kw)
    r = audit(spec.load(SHOP), k=1, include_review=True, only=set(only), review=s,
              keep_going=True)
    return r, {f.rule_id: f for f in r.findings}


def _strip(script, n):
    return [s for i, s in enumerate(script) if i != n]


# -- the two judges -------------------------------------------------------------------------

def test_strength_proves_a_wrong_implementation_passes():
    agent = Agent(strength=STRENGTH)
    r, fs = _run(agent)
    f = fs["RG110"]
    assert f.verdict is VerdictTier.CONFIRMED
    assert "adds 2 instead of doubling" in f.evidence.detail and CHECK_DOUBLE in f.evidence.detail
    assert f.evidence.replay and f.evidence.replay.startswith("rlgate repro RG110")
    c = next(c for c in r.checks if c.probe_id == "agentic.strength")
    assert c.status == CheckRun.FLAGGED and "2 requirements recorded" in c.note


def test_strength_never_sees_the_tests():
    agent = Agent(strength=[[("run", {"command": "ls .graded"})], [("finish", {})]])
    _run(agent)
    assert "read_test" not in agent.tools["strength"]
    assert "test_shop.py" not in agent.prompts["strength"]
    assert agent.results["strength"][0].startswith("exit code ")
    assert not agent.results["strength"][0].startswith("exit code 0")   # not copied in


def test_a_check_that_fails_on_the_reference_proves_nothing():
    bad = [*STRENGTH[:2], [("try_bypass", {
        "requirement_id": "R2", "how": "adds 2",
        "check_cmd": 'python3 -c "import shop; assert shop.double(3) == 7"'})],
        [("finish", {})]]
    agent = Agent(strength=bad)
    _, fs = _run(agent)
    assert "RG110" not in fs
    assert any("also fails on the reference" in x for x in agent.results["strength"])


def test_requirements_are_frozen_once_grading_starts():
    late = [*STRENGTH[:3], [("record_requirements", {"requirements": REQS})],
            [("finish", {})]]
    agent = Agent(strength=late)
    _run(agent)
    assert any("fixed once grading starts" in x for x in agent.results["strength"])


def test_requirement_quotes_must_be_verbatim():
    made_up = [{"id": "R9", "text": "x", "quote": "returns x tripled", "source": "statement"}]
    agent = Agent(strength=[[("record_requirements", {"requirements": made_up})],
                            [("finish", {})]])
    _run(agent)
    assert "not verbatim in statement" in agent.results["strength"][0]


def test_ambiguity_proves_a_correct_alternative_is_rejected():
    agent = Agent(ambiguity=AMBIGUITY)
    _, fs = _run(agent)
    f = fs["RG408"]
    assert f.verdict is VerdictTier.CONFIRMED and f.location == "test_logs"
    assert "test_logs" in f.evidence.detail and "every other graded test passes" in \
        f.evidence.detail
    assert "read_test" in agent.tools["ambiguity"]
    assert f.evidence.replay and f.evidence.replay.startswith("rlgate repro RG408")


def test_an_alternative_that_is_not_otherwise_complete_proves_nothing():
    broken = QUIET.replace("return x * 2", "return x * 3")
    script = [[("write_file", {"path": "shop.py", "content": broken})], AMBIGUITY[1],
              [("finish", {})]]
    agent = Agent(ambiguity=script)
    _, fs = _run(agent)
    assert "RG408" not in fs
    assert any("not otherwise complete" in x for x in agent.results["ambiguity"])


def test_the_agent_cannot_touch_tests_or_leave_the_repo():
    agent = Agent(strength=[[("write_file", {"path": "test_shop.py", "content": "x"}),
                             ("write_file", {"path": ".graded/x.py", "content": "x"}),
                             ("read_file", {"path": "../task.rlgate.yaml"})],
                            [("finish", {})]])
    _run(agent)
    out = agent.results["strength"]
    assert sum("is a test file" in x for x in out) == 2
    assert any("relative to the repository root" in x for x in out)


# -- fail closed ---------------------------------------------------------------------------

def test_an_agent_that_never_finishes_is_unfinished():
    agent = Agent(strength=[[("list_dir", {})]] * 3)
    r, _ = _run(agent, only={"agentic.strength"}, agent_turns=3)
    c = next(c for c in r.checks if c.probe_id == "agentic.strength")
    assert c.status == CheckRun.UNFINISHED and "without finishing" in c.reason


def test_quick_runs_leave_the_agent_out():
    s = Session(["openai/gpt-x"], client=Agent(), votes=1)
    r = audit(spec.load(SHOP), static_only=True, include_review=True, only=L3, review=s)
    assert {c.status for c in r.checks if c.probe_id in L3} == {CheckRun.SKIPPED_LIVE}


# -- confirming L2 rejections ----------------------------------------------------------------

WEAK = {"verdict": "problem", "negatives": [{
    "test": "test_double", "quote": "assert shop.double(2) == 4", "quote_from": "tests",
    "gap": "double is checked only with x=2, so `return x + 2` passes", "confidence": 0.9,
    "detail": "def double(x): return x + 2"}]}
DETERMINE = {"verdict": "problem", "negatives": [{
    "test": "test_logs", "quote": 'assert "classify called" in buf.getvalue()',
    "quote_from": "tests", "gap": "test_logs expects the print \"classify called\" that the "
    "statement never mentions", "confidence": 0.9, "detail": "state it"}]}


def test_a_weak_pass_lead_the_agent_proves_becomes_proven():
    script = [*STRENGTH[:2], [("try_bypass", {"requirement_id": "R2", "how": "adds 2",
                                              "check_cmd": CHECK_DOUBLE, "lead": "L1"})],
              [("finish", {})]]
    agent = Agent(strength=script, l2={"weak_pass": WEAK})
    r, fs = _run(agent, only=L3 | {"review.weak_pass"})
    assert "L1:" in agent.prompts["strength"] and "return x + 2" in agent.prompts["strength"]
    assert "RG109" not in fs and "first-pass lead L1 (RG109)" in fs["RG110"].evidence.detail
    c = next(c for c in r.checks if c.probe_id == "review.weak_pass")
    assert "L3 proved 1 by running it" in c.note


def test_a_weak_pass_lead_the_verifier_rejects_is_dropped():
    wrong = GOOD_CLASSIFY + "\n\ndef double(x):\n    return 0\n"
    script = [[("record_requirements", {"requirements": REQS})],
              [("write_file", {"path": "shop.py", "content": wrong})],
              [("try_bypass", {"requirement_id": "R2", "how": "returns 0",
                               "check_cmd": CHECK_DOUBLE, "lead": "L1"})],
              [("finish", {})]]
    r, fs = _run(Agent(strength=script, l2={"weak_pass": WEAK}),
                 only=L3 | {"review.weak_pass"})
    assert "RG109" not in fs and "RG110" not in fs
    c = next(c for c in r.checks if c.probe_id == "review.weak_pass")
    assert "verifier rejected them" in c.note


def test_an_untried_lead_still_needs_judgment():
    _, fs = _run(Agent(l2={"weak_pass": WEAK}), only=L3 | {"review.weak_pass"})
    assert fs["RG109"].verdict is VerdictTier.REVIEW
    assert "L3 did not settle it" in fs["RG109"].evidence.detail


def test_a_determinability_gap_the_repo_answers_is_dropped(tmp_path):
    import shutil
    root = tmp_path / "shop"
    shutil.copytree(SHOP, root)
    (root / "repo" / "README.md").write_text("Every public function prints "
                                             "\"<name> called\" for the audit log.\n")
    script = [[("resolve_lead", {"lead": "L1", "resolved": True, "file": "README.md",
                                 "quote": "prints \"<name> called\"", "reason": "README"})],
              [("finish", {})]]
    agent = Agent(ambiguity=script, l2={"determinability": DETERMINE})
    s = Session(["openai/gpt-x"], client=agent, votes=1)
    r = audit(spec.load(str(root)), k=1, include_review=True,
              only={"review.determinability", "agentic.ambiguity"}, review=s,
              keep_going=True)
    assert not [f for f in r.findings if f.rule_id == "RG704"]
    c = next(c for c in r.checks if c.probe_id == "review.determinability")
    assert "the repository answers" in c.note and "README.md" in c.note


def test_a_made_up_repo_quote_does_not_resolve_a_lead():
    script = [[("resolve_lead", {"lead": "L1", "resolved": True, "file": "shop.py",
                                 "quote": "print the audit line", "reason": "x"})],
              [("finish", {})]]
    agent = Agent(ambiguity=script, l2={"determinability": DETERMINE})
    _, fs = _run(agent, only={"review.determinability", "agentic.ambiguity"})
    assert "RG704" in fs
    assert any("not verbatim" in x for x in agent.results["ambiguity"])


def test_estimate_adds_the_agent_on_full_runs():
    s = Session(["openai/gpt-x"], client=Agent(), votes=1, agent_turns=10)
    task = spec.load(SHOP)
    # two L3 audits and three L4 hack runs, 10 turns each at most
    assert s.estimate([task], agentic=True).calls == s.estimate([task]).calls + 50


def test_the_agent_is_told_its_turns_and_reminded_before_they_run_out():
    seen: list[list[dict]] = []

    class Explorer(Agent):
        def chat(self, messages, tools):
            seen.append(list(messages))
            return super().chat(messages, tools)

    agent = Explorer(strength=[[("list_dir", {})]] * 8)
    _run(agent, only={"agentic.strength"}, agent_turns=8)
    assert "8 turns in all" in agent.prompts["strength"]
    reminders = [m for m in seen[-1] if m["role"] == "user" and "nothing graded" in
                 m["content"]]
    assert len(reminders) == 1


def test_l2_claims_settled_by_execution_are_counted():
    from rlgate.core import claims
    script = [*STRENGTH[:2], [("try_bypass", {"requirement_id": "R2", "how": "adds 2",
                                              "check_cmd": CHECK_DOUBLE, "lead": "L1"})],
              [("finish", {})]]
    _run(Agent(strength=script, l2={"weak_pass": WEAK}), only=L3 | {"review.weak_pass"})
    assert claims.tally()["weak_pass"] == {"confirmed": 1, "refuted": 0, "unsettled": 0}
    assert claims.summary() == "weak pass: 1 of 1 held up (100%)"
    # a re-check of the same claim replaces its outcome instead of counting twice
    _run(Agent(l2={"weak_pass": WEAK}), only=L3 | {"review.weak_pass"})
    assert claims.tally()["weak_pass"] == {"confirmed": 0, "refuted": 0, "unsettled": 1}
