"""The per-test map flows baseline -> engine: gold report consolidation (incl. flaky), and
report-driven variant classification + subset execution. Uses a fake sandbox so the wiring is
tested without a container."""

from __future__ import annotations

from pathlib import Path

from rlgate.baseline.invariant import establish
from rlgate.core.context import Baseline
from rlgate.core.task import GraderSpec, Patch, ReportSpec, Task
from rlgate.sandbox.base import ExecResult, Overlay, Perms, Sandbox
from rlgate.variants.engine import VariantClass, VariantEngine
from rlgate.variants.revert import Variant

_BODY = {"pass": "", "fail": "<failure/>", "skip": "<skipped/>"}


def junit(statuses: dict[str, str]) -> str:
    rows = "".join(
        f'<testcase classname="m.T" name="{n}">{_BODY[s]}</testcase>'
        for n, s in statuses.items()
    )
    return f"<testsuites><testsuite>{rows}</testsuite></testsuites>"


class FakeOverlay(Overlay):
    def __init__(self, run: ExecResult, report: str | None, record: list[str]) -> None:
        self.root = Path(".")
        self._run, self._report, self._record = run, report, record

    def exec(self, cmd: str, timeout: int = 900) -> ExecResult:
        self._record.append(cmd)
        if cmd.startswith("cat "):
            return ExecResult(0, self._report, "") if self._report else ExecResult(1, "", "")
        return self._run

    def read_perms(self, path: str) -> Perms:
        return Perms(mode=0o644)

    def close(self) -> None:
        pass


class FakeSandbox(Sandbox):
    """Gold overlays (patch present) consume `gold_reports` in order; nop overlays score 0."""

    def __init__(self, gold_reports: list[str | None], gold_exit: int = 0,
                 nop_exit: int = 1) -> None:
        self.gold_reports = list(gold_reports)
        self.gold_exit, self.nop_exit = gold_exit, nop_exit
        self._i = 0
        self.record: list[str] = []

    def build(self) -> None:
        pass

    def overlay(self, patch_text: str | None = None) -> FakeOverlay:
        if patch_text is None:
            return FakeOverlay(ExecResult(self.nop_exit, "", ""), None, self.record)
        rep = self.gold_reports[self._i]
        self._i += 1
        return FakeOverlay(ExecResult(self.gold_exit, "", ""), rep, self.record)

    def pkill_user(self, user: str) -> None:
        pass


def _task(select: str | None = None) -> Task:
    return Task(
        id="t", source="fake", repo_dir=Path("."),
        gold_patch=Patch("PATCH"),
        grader=GraderSpec(
            run="grade", report=ReportSpec(format="junit", path="r.xml"), select=select
        ),
    )


def test_baseline_consolidates_per_test_and_flags_flaky():
    # testA stable pass; testB flips pass->fail across the k gold runs -> flaky, excluded.
    reports = [
        junit({"testA": "pass", "testB": "pass"}),
        junit({"testA": "pass", "testB": "fail"}),
        junit({"testA": "pass", "testB": "pass"}),
    ]
    b = establish(_task(), FakeSandbox(reports), k=3)
    assert b.oracle == 1.0 and b.nop == 0.0
    assert b.per_test == {"m.T.testA": True}
    assert "m.T.testB" in b.flaky_checks
    assert not b.sane  # a flaky check makes the baseline not sane


def _engine(report: str | None, select: str | None = None) -> tuple[VariantEngine, FakeSandbox]:
    baseline = Baseline(
        oracle=1.0, nop=0.0, k=1,
        per_test={"m.T.t1": True, "m.T.t2": True, "m.T.t3": True},
    )
    sb = FakeSandbox([report])
    return VariantEngine(_task(select), sb, baseline), sb


def _variant() -> Variant:
    return Variant(id="v0", operator="revert", patch_text="PATCH", origin="f.py#hunk0")


def test_survivor_is_exploit():
    eng, _ = _engine(junit({"t1": "pass", "t2": "pass", "t3": "pass"}))
    r = eng._score(_variant())
    assert r.klass is VariantClass.DISCRIMINATED and r.is_exploit and r.broke == []


def test_partial_break_discriminates_without_exploit():
    eng, _ = _engine(junit({"t1": "fail", "t2": "pass", "t3": "pass"}))
    r = eng._score(_variant())
    assert r.klass is VariantClass.DISCRIMINATED and not r.is_exploit
    assert r.broke == ["m.T.t1"]


def test_all_break_is_failed_all():
    eng, _ = _engine(junit({"t1": "fail", "t2": "fail", "t3": "fail"}))
    r = eng._score(_variant())
    assert r.klass is VariantClass.FAILED_ALL and not r.is_exploit


def test_subset_command_runs_only_gold_passing_tests():
    eng, sb = _engine(junit({"t1": "pass", "t2": "pass", "t3": "pass"}),
                      select="run {tests}")
    eng._score(_variant())
    assert "run m.T.t1 m.T.t2 m.T.t3" in sb.record       # subset, sorted
    assert "grade" not in sb.record                        # full suite was skipped
