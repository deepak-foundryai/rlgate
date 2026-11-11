"""The L0 gate: oracle==1.0 / nop==0.0 at k runs, flaky detection, fail-to-pass and
pass-to-pass, timings against the task's limits, and the reference graded through the
extraction path. Fail-fast — if the baseline is not sane, probes that assume a sane baseline
are skipped (they'd be noise).

When the grader emits a per-test report, the gold and do-nothing runs' per-test maps (test id
-> passed) are consolidated across the k runs; a test whose pass/fail flips is flaky. The gold
map is the search space the variant engine runs its subset against; gold vs do-nothing gives
fail-to-pass (fails without the fix, passes with it) and pass-to-pass (passes either way).
"""

from __future__ import annotations

import threading
import time

from rlgate.core import repro, rules
from rlgate.core.context import Baseline
from rlgate.core.finding import Evidence, Finding, VerdictTier
from rlgate.core.grade import run_grader
from rlgate.core.task import Task
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.parsers import TestReport, TestStatus

NEAR_LIMIT = 0.8     # a run using more than this share of its time limit is flagged


def establish(task: Task, sandbox: Sandbox, k: int = 5, workers: int = 1) -> Baseline:
    from rlgate import ui
    from rlgate.variants.engine import pmap

    # k reference runs and k do-nothing runs, ``workers`` at a time; each is independent
    # (every reference run after the first re-runs the solution script from scratch).
    # Early stop: the verdict uses the reference's WORST run and do-nothing's BEST, so once
    # two runs agree that the reference fails (or doing nothing passes), more runs can't
    # make the task sane: the rest are skipped. A disagreement means flaky: run all k.
    ref = has_reference(task, sandbox)
    done = [0]
    lock = threading.Lock()

    def one(job: tuple[bool, int]) -> tuple[float, TestReport | None, float]:
        gold, i = job
        if workers <= 1:
            ui.progress(f"{'reference solution' if gold else 'do-nothing submission'}, "
                        f"run {i + 1} of {k}")
        r = _run(task, sandbox, gold=gold, fresh=gold and i > 0)
        with lock:
            done[0] += 1
        if workers > 1:
            ui.progress(f"reference and do-nothing runs: {done[0]} of {2 * k} done")
        return r

    def bad(gold: bool, reward: float) -> bool:
        return reward < task.grader.pass_reward if gold else reward > 0.0

    jobs = ([(True, 0)] if ref else []) + [(False, 0)]
    results = pmap(one, jobs, workers)
    first = {g: r[0] for (g, _), r in zip(jobs, results, strict=True)}
    failing = [g for g, reward in first.items() if bad(g, reward)]
    early = False
    if failing and k > 2:
        again = [(g, 1) for g in failing]
        more = pmap(one, again, workers)
        jobs, results = jobs + again, results + more
        early = all(bad(g, r[0]) and r[0] == first[g]
                    for (g, _), r in zip(again, more, strict=True))
    if not early:
        rest = [(g, i) for g in ([True, False] if ref else [False]) for i in range(k)
                if (g, i) not in set(jobs)]
        jobs, results = jobs + rest, results + pmap(one, rest, workers)

    grade_secs: list[float] = []
    oracle_runs: list[float] = []
    oracle_reports: list[TestReport | None] = []
    nop_runs: list[float] = []
    nop_reports: list[TestReport | None] = []
    for (gold, _), (reward, report, secs) in zip(jobs, results, strict=True):
        (oracle_runs if gold else nop_runs).append(reward)
        (oracle_reports if gold else nop_reports).append(report)
        grade_secs.append(secs)

    flaky: set[str] = set()
    if len(set(oracle_runs)) > 1:
        flaky.add("oracle")
    if len(set(nop_runs)) > 1:
        flaky.add("nop")

    per_test, test_flaky = _consolidate(oracle_reports)
    nop_per_test, nop_flaky = _consolidate(nop_reports)
    flaky |= test_flaky | nop_flaky

    solve = getattr(sandbox, "solution_seconds", None) or []
    b = Baseline(
        # worst-case; a single miss is a flaky/unfair signal. No reference: a placeholder
        oracle=min(oracle_runs) if ref else task.grader.pass_reward,
        reference=ref,
        nop=max(nop_runs), k=max(len(oracle_runs), len(nop_runs)), early=early,
        flaky_checks=frozenset(flaky), per_test=per_test,
        pass_reward=task.grader.pass_reward,
        oracle_runs=tuple(oracle_runs), nop_runs=tuple(nop_runs),
        nop_per_test=nop_per_test,
        reports=any(r is not None for r in oracle_reports)
        and any(r is not None for r in nop_reports),
        grade_seconds=max(grade_secs, default=0.0),
        grade_limit=float(task.grader.timeout_s),
        solve_seconds=max(solve, default=0.0),
        solve_limit=float(task.meta.get("agent_timeout_s") or 0.0) if solve else 0.0,
    )
    if ref and b.oracle >= b.pass_reward and uses_oracle_script(task, sandbox):
        ui.progress("reference changes extracted as a patch, graded alone")
        b.extracted = _extracted(task, sandbox, b)
    return b


def _run(task: Task, sandbox: Sandbox, gold: bool,
         fresh: bool = False) -> tuple[float, TestReport | None, float]:
    if gold and uses_oracle_script(task, sandbox):
        with sandbox.oracle_overlay(fresh=fresh) as ovl:     # type: ignore[attr-defined]
            t = time.monotonic()
            g = run_grader(ovl, task)
            return g.reward, g.report, time.monotonic() - t
    patch = task.gold_patch.text if (gold and task.gold_patch) else None
    with sandbox.overlay(patch) as ovl:
        t = time.monotonic()
        g = run_grader(ovl, task)
        return g.reward, g.report, time.monotonic() - t


def _extracted(task: Task, sandbox: Sandbox, b: Baseline) -> float:
    """Training grades a submission as a patch of the workspace, applied to a fresh
    environment. Grade the reference that way: its workspace changes only."""
    if task.gold_patch is None:          # it changed nothing inside the workspace
        return b.nop
    with sandbox.overlay(task.gold_patch.text) as ovl:
        return run_grader(ovl, task).reward


def has_reference(task: Task, sandbox: Sandbox | None = None) -> bool:
    """The task ships a reference solution rlgate can run (a patch or a solution script)."""
    return task.gold_patch is not None or uses_oracle_script(task, sandbox) or (
        sandbox is None and task.oracle is not None)


def uses_oracle_script(task: Task, sandbox: Sandbox | None) -> bool:
    """The reference is a solution SCRIPT the sandbox can run as the harness would — the
    true oracle (a derived patch can't carry effects outside the workspace)."""
    return task.oracle is not None and hasattr(sandbox, "oracle_overlay")


def findings(task: Task, baseline: Baseline) -> list[Finding]:
    """RG001 gold fails / RG002 do-nothing passes / RG003 flaky / RG006 no fail-to-pass test /
    RG007 the fix breaks a passing test / RG008 no pass-to-pass test / RG009 the fix doesn't
    survive extraction / RG011 too close to a time limit — each with a proof."""
    pass_reward = task.grader.pass_reward
    gold = task.gold_patch.text if task.gold_patch else None
    script = task.oracle is not None and task.staged
    out: list[Finding] = []
    if baseline.reference and baseline.oracle < pass_reward:
        replay = repro.record(task, "RG001", patch=None if script else gold, expect="fail",
                              oracle=script,
                              note=("run the reference solution script and grade" if script
                                    else "apply the reference fix and grade"))
        out.append(_f("RG001", f"the reference solution scored {baseline.oracle} "
                               f"(want {pass_reward}) on its worst of {baseline.k} runs"
                               + _early(baseline),
                      replay, "reproduced_exploit"))
    if baseline.nop > 0.0:
        replay = repro.record(task, "RG002", patch=None, expect="pass",
                              note="grade the unchanged workspace")
        out.append(_f("RG002", f"the unchanged workspace scored {baseline.nop} "
                               f"(want 0.0) on its best of {baseline.k} runs"
                               + _early(baseline),
                      replay, "reproduced_exploit"))
    if baseline.flaky_checks:
        flaky = sorted(baseline.flaky_checks)
        out.append(_f("RG003", f"results changed across {baseline.k} identical runs: "
                               f"{_some(flaky)}", None, "variant"))

    if baseline.reference and baseline.reports and baseline.per_test:
        if not baseline.fail_to_pass and baseline.oracle >= pass_reward:
            out.append(_f("RG006", f"none of the {len(baseline.per_test)} tests in the "
                                   "grader's report fails without the fix and passes with it",
                          None, "variant"))
        broken = baseline.broken_by_fix
        if broken:
            ignored = baseline.oracle >= pass_reward
            out.append(_f("RG007", f"{len(broken)} test(s) pass on the unchanged task and fail "
                                   f"with the reference fix: {_some(broken)}"
                                   + ("; the grade still gives full reward" if ignored else ""),
                          None, "variant", location=broken[0]))
        if not baseline.pass_to_pass and baseline.fail_to_pass:
            out.append(_f("RG008", f"all {len(baseline.per_test)} graded tests fail without "
                                   "the fix, so none checks that existing behaviour still "
                                   "works", None, "variant", verdict=VerdictTier.REVIEW))

    if baseline.extracted is not None and baseline.extracted < pass_reward:
        what = ("the reference solution changes no file in the workspace"
                if gold is None else
                f"its workspace changes, applied as a patch to a fresh environment, scored "
                f"{baseline.extracted} (want {pass_reward})")
        replay = repro.record(task, "RG009", patch=gold, expect="pass",
                              note="apply the reference's extracted patch and grade") \
            if gold else None
        out.append(_f("RG009", f"the reference solution passes when run in place, but {what}",
                      replay, "reproduced_exploit"))

    for label, secs, limit in (("grading", baseline.grade_seconds, baseline.grade_limit),
                               ("the reference solution", baseline.solve_seconds,
                                baseline.solve_limit)):
        if limit and secs > NEAR_LIMIT * limit:
            out.append(_f("RG011", f"{label} took {secs:.0f}s of its {limit:.0f}s limit "
                                   f"({secs / limit:.0%})", None, "variant"))
    return out


def _early(b: Baseline) -> str:
    return (" (the other runs were skipped: two agreeing runs settle it, more can't change "
            "the verdict)" if b.early else "")


def _some(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


def _f(rule_id: str, detail: str, replay: str | None, kind: str, *, location: str = "",
       verdict: VerdictTier = VerdictTier.CONFIRMED) -> Finding:
    rule = rules.get(rule_id)
    return Finding(
        probe_id="baseline.invariant",
        rule_id=rule_id,
        title=rule.title,
        severity=rule.severity,
        verdict=verdict,
        location=location,
        evidence=Evidence(kind=kind, detail=detail, replay=replay),
        fix_hint=rule.fix,
    )


def _consolidate(
    reports: list[TestReport | None],
) -> tuple[dict[str, bool], set[str]]:
    """Merge k reports into {test id -> passed} + the set of flaky (flipping) tests.

    A test passes iff every run where it appeared recorded PASS (SKIP is ignored). A test that
    is both PASS and FAIL across runs is flaky; it is excluded from the usable per-test map."""
    present = [r for r in reports if r is not None]
    if not present:
        return {}, set()

    seen: dict[str, set[TestStatus]] = {}
    for report in present:
        for name, status in report.status_by_name.items():
            seen.setdefault(name, set()).add(status)

    per_test: dict[str, bool] = {}
    flaky: set[str] = set()
    for name, statuses in seen.items():
        graded = statuses - {TestStatus.SKIP}
        if TestStatus.PASS in graded and TestStatus.FAIL in graded:
            flaky.add(name)
            continue
        if graded:
            per_test[name] = graded == {TestStatus.PASS}
    return per_test, flaky
