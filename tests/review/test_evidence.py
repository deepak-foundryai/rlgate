"""L2 determinability reads the repository too: tested details the statement doesn't name
are looked up in the pre-change repository and shown to the judge."""

from __future__ import annotations

from rlgate.core.task import GraderSpec, Task
from rlgate.review.bundle import build
from rlgate.review.evidence import details

TEST = '''from app import svc

def test_duplicate(client):
    payload = {"guest": "Ann"}
    r = client.post("/rsvp", json=payload)
    assert r.json()["code"] == "duplicate_rsvp"
    assert svc.secondary_events_rsvp(r) is None
    assert r.json()["guest"] == "Ann"
    assert r.ok, "the second RSVP must be refused with a clear code"
'''


def _task(tmp_path, statement="Refuse a second RSVP for the same guest.") -> Task:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "errors.py").write_text('DUPLICATE = "duplicate_rsvp"\n')
    (repo / "tests").mkdir()
    (repo / "tests" / "test_rsvp.py").write_text(TEST)      # a copy left in the workspace
    held = tmp_path / "held"
    held.mkdir()
    (held / "test_rsvp.py").write_text(TEST)
    return Task(id="t", source="test", instruction=statement, repo_dir=repo,
                grader=GraderSpec(run="pytest /tests", verdict="exit_code",
                                  inject=((str(held), "/tests"),)))


def test_tested_details_skip_test_inputs_messages_and_the_statement():
    terms = dict(details({"tests/test_rsvp.py": TEST}, "Add secondary_events_rsvp."))
    assert "duplicate_rsvp" in terms
    assert terms["duplicate_rsvp"] == ("tests/test_rsvp.py::test_duplicate",)
    assert "Ann" not in terms                       # the test's own input
    assert not any("refused" in t for t in terms)   # an assertion message
    assert "secondary_events_rsvp" not in terms     # the statement names it


def test_second_pass_shows_where_the_repo_explains_an_edge_case(tmp_path):
    b = build(_task(tmp_path))
    by = {d.term: d for d in b.evidence}
    assert [(h.path, h.line) for h in by["duplicate_rsvp"].hits] == [("app/errors.py", 1)]
    assert by["secondary_events_rsvp"].hits == ()     # nowhere: a real gap
    hidden = b.hidden.render(repo=True)
    assert "REPOSITORY EVIDENCE FOR TESTED DETAILS" in hidden
    assert "app/errors.py:1: DUPLICATE" in hidden
    assert "not found in the pre-change repository" in hidden
    assert "REPOSITORY EVIDENCE" not in b.render(repo=False)
    assert 'DUPLICATE = "duplicate_rsvp"' in b.source("repo")   # quotable


def test_a_graded_test_never_explains_itself(tmp_path):
    b = build(_task(tmp_path))
    paths = {h.path for d in b.evidence for h in d.hits}
    assert "tests/test_rsvp.py" not in paths
