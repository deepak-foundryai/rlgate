"""L4 hack agent, end to end on the shop fixture with a scripted agent (no network). The
"container" is the host backend under another name, so grading is real; the scripted agent
only writes inside the workspace.

Each slice of the taxonomy gets its own run: a forged pass through a root conftest.py
(RG215), wrong code that passes (RG111, proven by the agent's check against the reference),
and a lookup that prints the reference fix (RG308).
"""

from __future__ import annotations

import json
import threading

import pytest

from rlgate.agentic import hack as H
from rlgate.core import repro
from rlgate.core.finding import VerdictTier
from rlgate.ingest import spec
from rlgate.llm import ChatTurn, LLMResult
from rlgate.pipeline import CheckRun, audit
from rlgate.review import judges as J
from rlgate.review.session import Session
from rlgate.sandbox.local import LocalSandbox

SHOP = "tests/fixtures/agentic/shop"
FORGE = '''import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        rep.outcome = "passed"
'''
WRONG = ('def classify(n):\n    print("classify called")\n    return "big" if n > 10 else '
         '"small"\n\n\ndef double(x):\n    return x + 2\n')
CHECK = 'python3 -c "import shop; assert shop.double(3) == 6"'
L4 = {"agentic.hack"}


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "state"))


class Container(LocalSandbox):
    """The host backend posing as a container: ``isolated`` is accepted, and the agent's
    environment may carry a leaked file."""

    leak: str | None = None

    def overlay(self, patch_text=None, *, isolated=False):
        return super().overlay(patch_text)

    def agent_env(self):
        ovl = self.overlay(None)
        if self.leak is not None:
            (ovl.root / ".cache").mkdir()
            (ovl.root / ".cache" / "fix.diff").write_text(self.leak)
        return ovl


class Hacker:
    """A scripted model: one script per taxonomy slice, told apart by the prompt."""

    def __init__(self, scripts: dict[str, list]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.results: dict[str, list[str]] = {k: [] for k in H.SLICES}
        self.prompts: dict[str, str] = {}
        self._lock = threading.Lock()

    def __call__(self, model):
        return self

    def chat(self, messages, tools):
        user = messages[1]["content"]
        kind = next(k for k, (q, _) in H.SLICES.items() if q in user)
        with self._lock:
            self.prompts[kind] = user
            self.results[kind] += [m["content"] for m in messages if m.get("role") == "tool"
                                   and m["content"] not in self.results[kind]]
            script = self.scripts.get(kind) or []
            step = script.pop(0) if script else [("finish", {"summary": "nothing"})]
        calls = [(f"c{len(messages)}_{i}", n, json.dumps(a)) for i, (n, a) in enumerate(step)]
        msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c, "type": "function", "function": {"name": n, "arguments": a}}
            for c, n, a in calls]}
        return ChatTurn(msg, calls, cost_usd=0.01)

    def complete(self, prompt, *, schema=None, **_):       # L2, if asked: all clean
        if "results" in (schema or {}).get("properties", {}):
            names = schema["properties"]["results"]["items"]["properties"]["criterion"]["enum"]
            art = next(a for a, cs in J.FIRST.items() if [c.name for c in cs] == names)
            data = {"results": [{"criterion": c.name, "verdict": "pass", "test": "",
                                 "quote": "", "quote_from": "", "gap": "", "confidence": 0.9,
                                 "fix": ""} for c in J.FIRST[art]]}
        else:
            data = {"verdict": "ok", "negatives": []}
        return LLMResult(text="{}", data=data, model="openai/gpt-x", cost_usd=0.0)

    def check(self):
        return None


SCRIPTS = {
    "grading_setup": [
        [("shell", {"command": "ls -a"})],
        [("write_file", {"path": "conftest.py", "content": FORGE})],
        [("submit", {"claim": "a root conftest.py marks every test passed",
                     "check_cmd": CHECK})],
        [("finish", {"summary": "forged"})]],
    "wrong_code": [
        [("write_file", {"path": "shop.py", "content": WRONG})],
        [("submit", {"claim": "double adds 2 instead of doubling", "check_cmd": CHECK})],
        [("finish", {"summary": "wrong code passes"})]],
    "lookup": [
        [("report_lookup", {"command": "cat .cache/fix.diff",
                            "what": "the reference diff is in a cache dir"})],
        [("finish", {"summary": "found it"})]],
}


def _run(agent, sandbox_cls=Container, leak=None, **kw):
    task = spec.load(SHOP)
    sb = sandbox_cls(task.repo_dir, task.grader.inject)
    if leak is not None:
        sb.leak = leak
    s = Session(["openai/gpt-x"], client=agent, votes=1, **kw)
    r = audit(task, sandbox=sb, k=1, include_review=True, only=set(L4), review=s,
              keep_going=True)
    return r, {f.rule_id: f for f in r.findings}


def test_each_slice_is_replayed_and_proven():
    task = spec.load(SHOP)
    agent = Hacker(SCRIPTS)
    r, fs = _run(agent, leak=task.gold_patch.text)

    forged = fs["RG215"]
    assert forged.verdict is VerdictTier.CONFIRMED and forged.location == "conftest.py"
    assert "score 1.0" in forged.evidence.detail and "alone score 0.0" in forged.evidence.detail
    assert forged.evidence.replay.startswith("rlgate repro RG215")

    wrong = fs["RG111"]
    assert wrong.verdict is VerdictTier.CONFIRMED and wrong.location == "shop.py"
    assert CHECK in wrong.evidence.detail and "passes on the reference" in wrong.evidence.detail

    leak = fs["RG308"]
    assert leak.verdict is VerdictTier.CONFIRMED and "cat .cache/fix.diff" in \
        leak.evidence.detail

    c = next(c for c in r.checks if c.probe_id == "agentic.hack")
    assert c.status == CheckRun.FLAGGED and "grading_setup:" in c.note
    assert any("reward 1.0 (PASS)" in x for x in agent.results["grading_setup"])
    # the agent sees the statement and the taxonomy, never the tests or the reference
    p = agent.prompts["wrong_code"]
    assert "Override __eq__" in p and "classify(n)" in p and "test_shop" not in p


def test_wrong_code_without_a_proof_needs_judgment():
    scripts = {"wrong_code": [
        [("write_file", {"path": "shop.py", "content": WRONG})],
        [("submit", {"claim": "double adds 2", "check_cmd": ""})],
        [("finish", {})]]}
    _, fs = _run(Hacker(scripts))
    assert "RG111" not in fs
    assert fs["RG113"].verdict is VerdictTier.REVIEW
    assert "no check command" in fs["RG113"].evidence.detail


def test_a_lookup_that_shows_nothing_proves_nothing():
    _, fs = _run(Hacker({"lookup": SCRIPTS["lookup"]}), leak="unrelated\n")
    assert "RG308" not in fs


def test_a_failed_hack_is_dropped():
    scripts = {"wrong_code": [
        [("write_file", {"path": "shop.py", "content": "def double(x):\n    return 0\n"})],
        [("submit", {"claim": "returns 0", "check_cmd": CHECK})],
        [("finish", {})]]}
    agent = Hacker(scripts)
    r, fs = _run(agent)
    assert not {"RG111", "RG113", "RG215", "RG308"} & set(fs)
    assert any("(fail)" in x for x in agent.results["wrong_code"])
    assert next(c for c in r.checks if c.probe_id == "agentic.hack").status == CheckRun.GOOD


def test_a_saved_hack_is_replayed_before_any_agent_runs():
    _run(Hacker({"wrong_code": SCRIPTS["wrong_code"]}))
    assert repro.find("shop", "RG111")
    again = Hacker({})
    r, fs = _run(again)
    assert "RG111" in fs and "saved hack still passes" in fs["RG111"].evidence.detail
    assert not again.prompts                       # no LLM call at all
    assert "not re-run" in next(c for c in r.checks if c.probe_id == "agentic.hack").note


def test_never_on_the_host_backend():
    r, _ = _run(Hacker(SCRIPTS), sandbox_cls=LocalSandbox)
    c = next(c for c in r.checks if c.probe_id == "agentic.hack")
    assert c.status == CheckRun.NA and "docker or daytona" in c.reason


def test_budgets_are_enforced():
    agent = H.HackAgent(spec.load(SHOP), None, "wrong_code", max_submits=1, max_shells=1)
    agent.run.shells = 1
    assert "used up" in agent.call("shell", {"command": "ls"})
    assert "nothing to submit" in agent.call("submit", {"claim": "x", "check_cmd": ""})


def test_lookup_matching():
    patch = ("+++ b/x.py\n+def double(x):\n+    return x * 2  # doubled\n"
             "+    raise ValueError('negative amount')\n+x\n")
    found, of, _ = H.lookup_matches("def double(x):\n    return x * 2  # doubled\n"
                                     "raise ValueError('negative amount')", patch)
    assert (found, of) == (3, 3) and H.lookup_proven(found, of)
    assert not H.lookup_proven(*H.lookup_matches("def double(x):", patch)[:2])


def test_verdicts_are_cached_by_task_grader_and_patch():
    from rlgate.core.grade import cached_grade

    task = spec.load(SHOP)
    sb = LocalSandbox(task.repo_dir, task.grader.inject)
    sb.build()
    try:
        first = cached_grade(sb, task, task.gold_patch.text)
        assert first.passed and first.report is not None
        sb.overlay = None                      # a second grading would crash: it must not run
        again = cached_grade(sb, task, task.gold_patch.text)
        assert again.passed and again.reward == first.reward
        assert again.report.status_by_name == first.report.status_by_name
    finally:
        sb.close()


def test_a_flaky_grader_is_never_cached():
    from rlgate.core.grade import cached_grade

    task = spec.load(SHOP)
    sb = LocalSandbox(task.repo_dir, task.grader.inject)
    sb.build()
    try:
        cached_grade(sb, task, None, deterministic=False)
        sb.overlay = None
        with pytest.raises(TypeError):
            cached_grade(sb, task, None, deterministic=False)
    finally:
        sb.close()
