"""Each parser turns a real-shaped report into the same TestReport model."""

from __future__ import annotations

import pytest

from rlgate.sandbox.parsers import ReportParseError, TestStatus, known_formats, parse_report

JUNIT = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="suite">
    <testcase classname="m.T" name="test_ok"/>
    <testcase classname="m.T" name="test_bad"><failure message="x">boom</failure></testcase>
    <testcase classname="m.T" name="test_err"><error>oops</error></testcase>
    <testcase classname="m.T" name="test_skip"><skipped/></testcase>
  </testsuite>
</testsuites>"""

GO_JSON = """
{"Action":"run","Package":"p","Test":"TestA"}
{"Action":"pass","Package":"p","Test":"TestA"}
{"Action":"run","Package":"p","Test":"TestB"}
{"Action":"fail","Package":"p","Test":"TestB"}
{"Action":"skip","Package":"p","Test":"TestC"}
{"Action":"pass","Package":"p"}
build noise that is not json
"""

TRX = """<?xml version="1.0"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testName="N.TestA" outcome="Passed"/>
    <UnitTestResult testName="N.TestB" outcome="Failed"/>
    <UnitTestResult testName="N.TestC" outcome="NotExecuted"/>
  </Results>
</TestRun>"""

VITEST = """{"testResults":[
  {"assertionResults":[
    {"fullName":"a ok","status":"passed"},
    {"fullName":"a bad","status":"failed"},
    {"fullName":"a todo","status":"todo"}
  ]}
]}"""


def test_all_formats_registered():
    assert {"junit", "go_json", "trx", "vitest_json"} <= set(known_formats())


@pytest.mark.parametrize(
    ("fmt", "text", "passed", "failed", "skipped"),
    [
        ("junit", JUNIT, {"m.T.test_ok"}, {"m.T.test_bad", "m.T.test_err"}, {"m.T.test_skip"}),
        ("go_json", GO_JSON, {"p.TestA"}, {"p.TestB"}, {"p.TestC"}),
        ("trx", TRX, {"N.TestA"}, {"N.TestB"}, {"N.TestC"}),
        ("vitest_json", VITEST, {"a ok"}, {"a bad"}, {"a todo"}),
    ],
)
def test_parse(fmt, text, passed, failed, skipped):
    r = parse_report(fmt, text)
    sbn = r.status_by_name
    assert {n for n, s in sbn.items() if s is TestStatus.PASS} == passed
    assert {n for n, s in sbn.items() if s is TestStatus.FAIL} == failed
    assert {n for n, s in sbn.items() if s is TestStatus.SKIP} == skipped


def test_outcome_reward_and_fraction():
    r = parse_report("junit", JUNIT)
    assert r.outcome_reward(1.0) == 0.0          # a failure is present
    assert r.pass_fraction == pytest.approx(1 / 3)  # 1 pass of 3 graded (skip excluded)
    clean = parse_report("vitest_json", '{"testResults":[{"assertionResults":'
                         '[{"fullName":"x","status":"passed"}]}]}')
    assert clean.outcome_reward(2.0) == 2.0


def test_unknown_and_malformed():
    with pytest.raises(ReportParseError):
        parse_report("nope", "")
    with pytest.raises(ReportParseError):
        parse_report("junit", "<not-xml")
    with pytest.raises(ReportParseError):
        parse_report("vitest_json", "{bad json")
