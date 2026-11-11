"""L2 static review with a scripted judge (no network): the bundle each pass sees, citation
checks, votes, the author-family rule, fail-closed behaviour, the rewrite loop, the cache, the
budget, and the CLI around --deep."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlgate.core.finding import VerdictTier
from rlgate.core.task import GraderSpec, Patch, Task
from rlgate.llm import LLMError, LLMResult
from rlgate.pipeline import CheckRun, audit
from rlgate.review import judges as J
from rlgate.review.bundle import build
from rlgate.review.cite import concrete, names_a_test, quoted
from rlgate.review.session import Session

REVIEW = {"review.statement", "review.tests", "review.reference", "review.verifier",
          "review.determinability", "review.enforcement", "review.weak_pass"}

STATEMENT = ("Add `discount(total, code)` to shop.py. Code SAVE10 takes 10% off; any other "
             "code leaves the total unchanged.\n")
TEST = '''from shop import discount


def test_save10():
    assert discount(100, "SAVE10") == 90


def test_unknown_code():
    assert discount(100, "NOPE") == 100


def test_message():
    assert discount.__doc__ == "Apply a discount code."
'''
FIX = '''--- a/shop.py
+++ b/shop.py
@@ -1,2 +1,6 @@
 def total(xs):
     return sum(xs)
+
+def discount(total, code):
+    """Apply a discount code."""
+    return total * 0.9 if code == "SAVE10" else total
'''
SCRIPT = "#!/bin/bash\nif pytest /tests; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\n"


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def task(tmp_path) -> Task:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "shop.py").write_text("def total(xs):\n    return sum(xs)\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_shop.py").write_text(TEST)
    (tests / "test.sh").write_text(SCRIPT)
    return Task(id="shop", source="test", repo_dir=ws, instruction=STATEMENT,
                gold_patch=Patch(FIX),
                grader=GraderSpec(run="bash /tests/test.sh", verdict="reward_file",
                                  reward_files=("/logs/verifier/reward.txt",),
                                  inject=((str(tests), "/tests"),)),
                meta={"metadata": {}})


# -- a scripted judge ---------------------------------------------------------------------

def _clean_first(artifact: str) -> dict:
    return {"results": [{"criterion": c.name, "verdict": "pass", "test": "", "quote": "",
                         "quote_from": "", "gap": "", "confidence": 0.9, "fix": ""}
                        for c in J.FIRST[artifact]]}


OK = {"verdict": "ok", "negatives": []}
DOC_NEG = {"test": "tests/test_shop.py::test_message",
           "quote": 'assert discount.__doc__ == "Apply a discount code."',
           "quote_from": "tests",
           "gap": "test_message pins the docstring \"Apply a discount code.\" which the "
                  "statement never mentions",
           "confidence": 0.9, "detail": "Drop the docstring assertion."}


class Judge:
    """Answers by schema: ``answers[(kind, name)]`` is a dict, a list (one per call) or an
    exception; anything unlisted is a clean answer."""

    def __init__(self, answers: dict | None = None, cost: float = 0.01) -> None:
        self.answers = answers or {}
        self.calls: list[dict] = []
        self.cost = cost

    def __call__(self, model: str):
        judge = self

        class Client:
            def complete(self, prompt, *, system=None, schema=None, schema_name="result",
                         seed=None, **_):
                judge.calls.append({"model": model, "prompt": prompt, "seed": seed,
                                    "schema": schema, "name": schema_name})
                kind, name = judge._which(prompt, schema or {}, schema_name)
                a = judge.answers.get((kind, name))
                if isinstance(a, list):
                    a = a.pop(0) if a else None
                if isinstance(a, Exception):
                    raise a
                if a is None:
                    a = (_clean_first(name) if kind == "first" else
                         OK if kind == "second" else {"statement": STATEMENT})
                return LLMResult(text="{}", data=a, model=model, cost_usd=judge.cost)

            def check(self):
                return None
        return Client()

    @staticmethod
    def _which(prompt: str, schema: dict, name: str) -> tuple[str, str]:
        if name == "rewrite":
            return "rewrite", "statement"
        if "results" in schema["properties"]:
            crit = schema["properties"]["results"]["items"]["properties"]["criterion"]["enum"]
            art = next(a for a, cs in J.FIRST.items() if [c.name for c in cs] == crit)
            return "first", art
        q = next(q for q, c in J.SECOND.items() if c.text in prompt)
        return "second", q


def _session(judge: Judge, models=("openai/gpt-x", "gemini/gemini-y", "anthropic/claude-z"),
             tmp: Path | None = None, **kw) -> Session:
    return Session(list(models), client=judge, cache_dir=tmp / "llm" if tmp else None,
                   out_dir=tmp / "review" if tmp else None, **kw)


def _run(task: Task, session: Session | None, only=REVIEW):
    return audit(task, static_only=True, include_review=True, only=set(only),
                 review=session, keep_going=True)


def _status(result) -> dict[str, str]:
    return {c.probe_id: c.status for c in result.checks if c.probe_id in REVIEW}


# -- the bundle ---------------------------------------------------------------------------

def test_second_pass_withholds_the_reference_fix(task):
    b = build(task)
    assert "return total * 0.9" in b.render(repo=False)
    second = b.hidden.render(repo=True)
    assert "REFERENCE FIX" not in second and "return total * 0.9" not in second
    assert "PRE-CHANGE FILE: shop.py" in second         # the test imports shop
    assert "tests/test_shop.py" in b.tests and "pytest /tests" in b.verifier
    assert "tests/test.sh" not in b.tests                # shown once, as the verifier


def test_author_notes_are_never_in_the_bundle(tmp_path):
    from rlgate.ingest import spec
    root = tmp_path / "notes-task"
    for rel, text in {
        "task.toml": ('version = "1.0"\n[metadata]\nsolution_explanation = "SECRET-HOW"\n'
                      'difficulty_explanation = "SECRET-WHY"\n[verifier]\ntimeout_sec = 60\n'
                      '[agent]\ntimeout_sec = 60\n[environment]\nbuild_timeout_sec = 60\n'),
        "instruction.md": "Make add() return the sum.\n",
        "environment/Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY app/ /app/\n",
        "environment/app/calc.py": "def add(a, b):\n    return 0\n",
        "solution/solve.sh": "#!/bin/bash\nsed -i 's/return 0/return a + b/' /app/calc.py\n",
        "tests/test.sh": "#!/bin/bash\npytest /tests/test_calc.py && echo 1 > "
                         "/logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n"
                              "    assert add(2, 3) == 5\n",
        "tests/scaffold/calc.py": "def add(a, b):\n    return 0\n",   # the app, mirrored
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    b = build(spec.load(str(root)))
    text = b.render(repo=True)
    assert "solution_explanation" not in text and "difficulty_explanation" not in text
    assert "SECRET-HOW" not in text and "SECRET-WHY" not in text
    assert not any(p.startswith("tests/scaffold/") for p in b.tests)


# -- citation checks ----------------------------------------------------------------------

def test_quotes_must_be_verbatim():
    text = 'def test_a():\n    assert   f(1) == 2\n    assert g("x") is None\n'
    assert quoted("assert f(1) == 2", text)                       # whitespace aside
    assert quoted('`assert g("x") is None`', text)               # quoting marks stripped
    assert quoted("def test_a(): ... assert g(\"x\") is None", text)
    assert not quoted("assert f(1) == 3", text)                   # made up
    assert not quoted("assert g(\"x\") is None ... def test_a():", text)   # wrong order


def test_gaps_must_be_concrete():
    q = 'assert resp.json()["status"] == "accepted"'
    assert concrete('the test expects status "accepted" but the statement never names it', q)
    assert not concrete("the statement could be clearer", q)
    assert not concrete("more detail is needed here", q)


def test_named_tests_must_exist(task):
    b = build(task)
    assert names_a_test("tests/test_shop.py::test_message", b.source("tests"))
    assert names_a_test("test_unknown_code", b.source("tests"))
    assert not names_a_test("tests/test_shop.py::test_refunds", b.source("tests"))


# -- the passes ---------------------------------------------------------------------------

def test_without_deep_the_llm_checks_wait_for_it(task):
    r = _run(task, None)
    assert set(_status(r).values()) == {CheckRun.SKIPPED_DEEP} and not r.findings


def test_clean_task_passes_every_llm_check(task):
    judge = Judge()
    r = _run(task, _session(judge))
    assert set(_status(r).values()) == {CheckRun.GOOD} and not r.findings
    assert len(judge.calls) == 4 + 3 * 3                  # 4 artifacts, 3 questions x 3 votes


def test_votes_are_independent_and_take_turns_through_the_panel(task):
    judge = Judge({("second", "determinability"): [
        {"verdict": "problem", "negatives": [DOC_NEG]}, OK,
        {"verdict": "problem", "negatives": [DOC_NEG]}]})
    r = _run(task, _session(judge), only={"review.determinability"})
    calls = [c for c in judge.calls if J.SECOND["determinability"].text in c["prompt"]]
    calls = calls[:3]               # the votes; the rewrite is re-reviewed after them
    assert sorted(c["seed"] for c in calls) == [1, 2, 3]      # votes run in parallel
    assert {c["model"] for c in calls} == {"openai/gpt-x", "gemini/gemini-y",
                                           "anthropic/claude-z"}
    assert all("return total * 0.9" not in c["prompt"] for c in calls)   # reference hidden
    (f,) = r.findings
    assert f.rule_id == "RG704" and f.verdict is VerdictTier.REVIEW
    assert f.location == "tests/test_shop.py"
    assert "2 of 3 judges" in f.evidence.detail and "Apply a discount code." in f.evidence.detail


def test_a_made_up_quote_is_discarded_and_counted(task):
    fake = dict(DOC_NEG, quote='assert discount(100, "VIP") == 50')
    judge = Judge({("second", "weak_pass"): {"verdict": "problem", "negatives": [fake]}})
    r = _run(task, _session(judge, votes=1), only={"review.weak_pass"})
    assert not r.findings
    (c,) = [c for c in r.checks if c.probe_id == "review.weak_pass"]
    assert c.status == CheckRun.GOOD and "quote not found in the files" in c.note


def test_weak_pass_names_the_cheap_implementation_for_l3(task):
    neg = {"test": "test_save10", "quote": 'assert discount(100, "SAVE10") == 90',
           "quote_from": "tests", "gap": "only discount(100, \"SAVE10\") is checked, so "
           "returning 90 for SAVE10 passes", "confidence": 0.8,
           "detail": "return 90 if code == 'SAVE10' else total"}
    judge = Judge({("second", "weak_pass"): {"verdict": "problem", "negatives": [neg]}})
    (f,) = _run(task, _session(judge, votes=1), only={"review.weak_pass"}).findings
    assert f.rule_id == "RG109"
    assert "return 90 if code == 'SAVE10'" in f.evidence.detail
    assert "L3 confirms this by running" in f.evidence.detail


def test_first_pass_findings_map_to_their_criterion(task):
    fail = {"criterion": "accepts_equivalents", "verdict": "fail",
            "test": "test_message", "quote": 'discount.__doc__ == "Apply a discount code."',
            "quote_from": "tests", "gap": "pins the reference's docstring \"Apply a discount "
            "code.\"", "confidence": 0.95, "fix": "Remove test_message."}
    ans = _clean_first("tests")
    ans["results"] = [r for r in ans["results"] if r["criterion"] != "accepts_equivalents"]
    ans["results"].append(fail)
    r = _run(task, _session(Judge({("first", "tests"): ans})), only={"review.tests"})
    (f,) = r.findings
    assert f.rule_id == "RG407" and f.fix_hint == "Remove test_message."


def test_integrity_defects_say_to_leave_the_task_out(task):
    ans = {"results": [{"criterion": "honest", "verdict": "fail", "test": "",
                        "quote": "if pytest /tests; then echo 1; else echo 0; fi",
                        "quote_from": "verifier", "gap": "made-up: claims `echo 1` runs "
                        "regardless", "confidence": 0.9, "fix": "x"}]}
    (f,) = _run(task, _session(Judge({("first", "verifier"): ans})),
                only={"review.verifier"}).findings
    assert f.rule_id == "RG107" and "leave the task out of training" in f.evidence.detail


# -- who may judge ------------------------------------------------------------------------

def test_judges_from_the_authors_family_are_left_out(task):
    task.meta["metadata"]["author_model"] = "anthropic/claude-opus-5-5"
    judge = Judge()
    _run(task, _session(judge))
    assert judge.calls and all(not c["model"].startswith("anthropic/") for c in judge.calls)


def test_no_judge_outside_the_authors_family_means_unfinished(task):
    task.meta["metadata"]["author_model"] = "openai/gpt-5"
    r = _run(task, _session(Judge(), models=("openai/gpt-x",)))
    assert set(_status(r).values()) == {CheckRun.UNFINISHED}
    c = next(c for c in r.checks if c.probe_id == "review.tests")
    assert "openai family" in c.reason


# -- fail closed --------------------------------------------------------------------------

def test_a_failed_call_is_never_a_pass(task):
    from rlgate.core import questions as Q
    from rlgate.core.rules import Step

    judge = Judge({("first", "tests"): LLMError("transient", "rate limited after retries")})
    r = _run(task, _session(judge))
    assert _status(r)["review.tests"] == CheckRun.UNFINISHED
    o = Q.outcome(r, quick=True)
    row = next(x for x in o.rows if x.step is Step.FAIRNESS)
    assert row.status == Q.PARTLY and "didn't finish" in row.summary
    assert not o.complete


def test_a_skipped_criterion_is_never_a_pass(task):
    ans = _clean_first("statement")
    ans["results"] = ans["results"][:1]
    r = _run(task, _session(Judge({("first", "statement"): ans})), only={"review.statement"})
    c = next(c for c in r.checks if c.probe_id == "review.statement")
    assert c.status == CheckRun.UNFINISHED and "did not judge" in c.reason


def test_one_failed_vote_without_a_negative_is_unfinished(task):
    judge = Judge({("second", "enforcement"): [OK, LLMError("transient", "timeout"), OK]})
    r = _run(task, _session(judge), only={"review.enforcement"})
    assert _status(r)["review.enforcement"] == CheckRun.UNFINISHED


def test_the_budget_stops_the_review(task):
    judge = Judge(cost=1.0)
    r = _run(task, _session(judge, budget_usd=2.0))
    assert len(judge.calls) == 2
    assert CheckRun.UNFINISHED in _status(r).values()


# -- the rewrite loop ---------------------------------------------------------------------

def test_a_fixable_statement_is_rewritten_and_re_reviewed(task, tmp_path):
    leak = _clean_first("statement")
    leak["results"][1] = {"criterion": "no_leak", "verdict": "fail", "test": "",
                          "quote": "Code SAVE10 takes 10% off", "quote_from": "statement",
                          "gap": "states the SAVE10 rate the `discount` fix hardcodes",
                          "confidence": 0.7, "fix": "Describe the behaviour."}
    better = "Add `discount(total, code)` to shop.py; see the pricing rules below.\n"
    judge = Judge({("first", "statement"): [leak, _clean_first("statement")],
                   ("rewrite", "statement"): {"statement": better}})
    (f,) = _run(task, _session(judge, tmp=tmp_path), only={"review.statement"}).findings
    assert f.rule_id == "RG702"
    path = tmp_path / "review" / "shop" / "instruction.rewritten.md"
    assert path.read_text() == better
    assert f.fix_hint and "passed re-review" in f.fix_hint and str(path) in f.fix_hint
    rewrite = next(c for c in judge.calls if c["name"] == "rewrite")
    assert "return total * 0.9" not in rewrite["prompt"]       # written without the fix


# -- cost and cache -----------------------------------------------------------------------

def test_answers_are_cached_on_disk(task, tmp_path):
    first = Judge()
    _run(task, _session(first, tmp=tmp_path))
    again = Judge()
    s = _session(again, tmp=tmp_path)
    _run(task, s)
    assert first.calls and not again.calls and s.cached == len(first.calls)


# -- settings and CLI ---------------------------------------------------------------------

def test_review_settings(tmp_path, monkeypatch):
    from rlgate import settings as S
    cfg = tmp_path / "rlgate.toml"
    cfg.write_text('[models]\nreview = ["openai/a", "gemini/b"]\nvotes = 5\n'
                   'author = "anthropic/claude-x"\nbudget_usd = 3\n')
    s = S.load(cfg)
    assert s.models.panel() == ["openai/a", "gemini/b"] and s.models.votes == 5
    assert s.models.author == "anthropic/claude-x" and s.models.budget_usd == 3
    monkeypatch.setenv("RLGATE_REVIEW_MODELS", "mistral/m, ollama/llama3")
    assert S.load(cfg).models.panel() == ["mistral/m", "ollama/llama3"]
    cfg.write_text("[models]\nvotes = 0\n")
    with pytest.raises(S.SettingsError, match="votes"):
        S.load(cfg)


def test_cli_model_needs_deep(capsys):
    from rlgate.cli import main
    assert main(["check", "examples/taskbank/clean", "--quick", "--model", "openai/x"]) == 2
    assert "--deep" in capsys.readouterr().err


def test_cli_deep_end_to_end_through_litellm(capsys, monkeypatch):
    import json
    from types import SimpleNamespace

    litellm = pytest.importorskip("litellm")
    from rlgate.cli import main

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = []

    def completion(**kw):
        seen.append(kw)
        schema = kw["response_format"]["json_schema"]["schema"]
        if "results" in schema["properties"]:
            names = schema["properties"]["results"]["items"]["properties"]["criterion"]["enum"]
            art = next(a for a, cs in J.FIRST.items() if [c.name for c in cs] == names)
            body = _clean_first(art)
        else:
            body = OK
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50), model="gpt-4o")

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "completion_cost", lambda completion_response: 0.002)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    # no question up front, even without a terminal; the total comes at the end
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(["check", "examples/taskbank/clean", "--quick", "--deep-review",
                 "--model", "openai/gpt-4o", "--fresh"])
    err = capsys.readouterr().err
    assert seen and all(kw["model"] == "openai/gpt-4o" for kw in seen)
    assert "LLM review:" in err and "tokens in" in err and "spent" in err
    assert "Run the LLM review?" not in err
    assert code in (0, 1)


# -- the three questions, alone ---------------------------------------------------------------

QUESTION_CHECKS = {"review.determinability", "review.enforcement", "review.weak_pass"}


def test_only_questions_selects_just_the_three_checks():
    from rlgate.cli.engine import select
    from rlgate.core.rules import Step

    sel = select(["questions"], [])
    assert sel.only == QUESTION_CHECKS
    assert sel.steps == {Step.GRADER.number, Step.FAIRNESS.number}
    assert select(["RG704"], []).only == {"review.determinability"}
    both = select(["leaks", "questions"], [])
    assert QUESTION_CHECKS <= both.only and Step.LEAKS.number in both.steps


def test_all_three_answered_yes(task):
    from rlgate.core import questions as Q

    got = Q.answers(_run(task, _session(Judge()), only=QUESTION_CHECKS))
    assert [a.answer for a in got] == [Q.YES, Q.YES, Q.YES]
    assert "3 calls" in got[0].votes


def test_one_negative_vote_makes_that_answer_no(task):
    from rlgate.core import questions as Q

    judge = Judge({("second", "determinability"): [
        OK, {"verdict": "problem", "negatives": [DOC_NEG]}, OK]})
    got = Q.answers(_run(task, _session(judge), only=QUESTION_CHECKS))
    assert [a.answer for a in got] == [Q.NO, Q.YES, Q.YES]
    assert got[0].detail.startswith("RG704 at ")


def test_a_question_without_a_verdict_is_never_yes(task):
    from rlgate.core import questions as Q

    judge = Judge({("second", "enforcement"): LLMError("unavailable", "down")})
    got = Q.answers(_run(task, _session(judge), only=QUESTION_CHECKS))
    assert got[1].answer == Q.UNFINISHED


def test_no_answers_without_deep(task):
    from rlgate.core import questions as Q

    assert Q.answers(_run(task, None, only=QUESTION_CHECKS)) is None


def test_cli_only_questions_needs_deep(capsys):
    from rlgate.cli import main

    assert main(["check", "examples/taskbank/clean", "--only", "questions"]) == 2
    assert "--deep" in capsys.readouterr().err


def test_one_problem_cited_with_different_test_names_is_one_group():
    from rlgate.review.cite import Negative

    q = "plus an optional `secondary_events_rsvp` list"
    a = Negative("enforcement", "gap", q, "statement", "tests/a.py::test_x", 0.9, "", "m1")
    b = Negative("enforcement", "gap", q, "statement", "none (no test sends it)", 0.8, "",
                 "m2")
    c = Negative("enforcement", "gap", "another quote", "statement", "test_x", 0.8, "", "m3")
    assert a.key == b.key != c.key
