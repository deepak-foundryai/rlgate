"""Orchestration in six numbered steps:

    1 Build → 2 Baseline → 3 Leaks → 4 Grader → 5 Cheating → 6 Fairness

Each step runs its checks (the probes whose rules fall in its range) and the run stops at the
first step with an open proven problem; later steps are "not run: fix step N first". Warnings
and needs-your-judgment items never stop it. ``keep_going`` carries on past a failure in
steps 3 to 6, and past a step 1-2 failure that leaves the build and the baseline sound (the
isolation rules in ``ISOLATION``); a broken build or baseline always stops (nothing after
it could be trusted).

A full run builds the environment in step 1 and grades the reference and a do-nothing
submission in step 2; a quick run does only the file-only part of each step. Steps a caller
marks as ``reuse`` come from an earlier result (their files didn't change), so a re-check
after a fix resumes at the failed step. Probes gate on capabilities only; the pipeline never
branches on benchmark identity.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from rlgate import ui
from rlgate.baseline.invariant import establish
from rlgate.baseline.invariant import findings as baseline_findings
from rlgate.core import rules
from rlgate.core.context import Baseline, ProbeContext
from rlgate.core.finding import Finding, VerdictTier
from rlgate.core.probe import CouldNotJudge, NotApplicable
from rlgate.core.registry import discover
from rlgate.core.rules import STEPS, Step
from rlgate.core.task import Task
from rlgate.review.session import Session
from rlgate.sandbox.base import Sandbox
from rlgate.sandbox.errors import BuildFailed
from rlgate.sandbox.select import build_sandbox
from rlgate.variants.engine import VariantEngine


@dataclass
class CheckRun:
    """One probe's outcome on one task — so a report can show what was *checked*, not just
    what was flagged. `status` is: good (ran, clean), flagged (ran, findings), skipped-live
    (needs a full run, not run in a quick one), skipped-baseline (the task doesn't work yet),
    n/a (capability/language gate: probe doesn't apply to this task), skipped-deep (an LLM
    check; runs with --deep-review) or unfinished (an LLM check that couldn't reach a verdict: never
    counted as passed)."""
    probe_id: str
    family: str
    status: str
    findings: int = 0
    doc: str = ""
    seconds: float = 0.0
    reason: str = ""                 # n/a / unfinished: why
    note: str = ""                   # LLM checks: calls, models, discarded negatives

    GOOD = "good"
    FLAGGED = "flagged"
    SKIPPED_LIVE = "skipped-live"
    SKIPPED_BASELINE = "skipped-baseline"
    NA = "n/a"
    SKIPPED_DEEP = "skipped-deep"
    UNFINISHED = "unfinished"


@dataclass
class StepRun:
    """What happened to one step in this run."""
    number: int
    state: str                       # ran | cached | not-run | skipped
    seconds: float = 0.0
    fingerprint: str = ""            # the step's input files when it ran (resume key)
    reason: str = ""                 # not-run / skipped: why

    RAN, CACHED, NOT_RUN, SKIPPED = "ran", "cached", "not-run", "skipped"

    @property
    def step(self) -> Step:
        return Step(self.number)


@dataclass
class AuditResult:
    task_id: str
    baseline_sane: bool
    findings: list[Finding]
    checks: list[CheckRun] = field(default_factory=list)
    baseline: Baseline | None = None           # None for a quick (file-only) run
    seconds: float = 0.0
    steps: list[StepRun] = field(default_factory=list)
    stopped_at: int | None = None              # the step the run stopped at


def _kept(findings: list[Finding], include_review: bool) -> list[Finding]:
    """By default the library asserts only what it has PROVEN; REVIEW findings are kept only
    when the caller opts in (the CLI always does)."""
    if include_review:
        return findings
    return [f for f in findings if f.verdict is VerdictTier.CONFIRMED]


# Proven problems in steps 1-2 about how the grader is isolated from the agent, not about
# whether the task builds or its baseline holds: later steps stay trustworthy past them.
ISOLATION = frozenset({"RG004", "RG010", "RG011", "RG012"})


def foundation_failed(findings: list[Finding]) -> bool:
    """A step 1-2 failure that later steps can't be trusted past."""
    from rlgate.core.questions import PROVEN, kind_of
    return any(kind_of(f) == PROVEN and f.rule_id not in ISOLATION for f in findings)


def step_failed(findings: list[Finding]) -> bool:
    """A step fails on an open (unwaived) proven problem; warnings never fail it."""
    from rlgate.core.questions import PROVEN, kind_of
    return any(kind_of(f) == PROVEN for f in findings)


def audit(task: Task, sandbox: Sandbox | None = None, k: int = 5,
          only: set[str] | None = None, backend: str = "auto",
          static_only: bool = False, include_review: bool = False,
          env_options: dict | None = None, *,
          steps: set[int] | None = None, keep_going: bool = False,
          prior: AuditResult | None = None, reuse: set[int] | None = None,
          fingerprints: dict[int, str] | None = None,
          on_findings: Callable[[list[Finding]], object] | None = None,
          workers: int = 1, depth: str = "fast", parallel: bool = False,
          review: Session | None = None) -> AuditResult:
    """Run the steps in order. ``steps``: the steps to run (None = all); ``reuse``: steps taken
    from ``prior`` unchanged; ``only``: restrict to these probe ids; ``on_findings`` marks
    waived findings before a step's pass/fail is decided.

    Speed: ``workers`` grading runs at a time inside a step (baseline runs, variants,
    plants); ``depth="fast"`` prunes redundant variants (``"full"`` grades every mutation
    site); ``parallel`` runs steps 3-6 at the same time once steps 1-2 pass, and reports every
    step at the end instead of stopping at the first failure.

    ``review``: the ``--deep-review`` LLM review panel (``rlgate.review.Session``). Without it the
    LLM checks are listed as "runs with --deep-review"."""
    run = _Run(task, sandbox, k, only, backend, static_only, include_review, env_options,
               fingerprints or {}, on_findings)
    run.workers, run.depth, run.review = max(1, workers), depth, review
    t0 = time.monotonic()
    try:
        run.go(steps, keep_going or parallel, prior, reuse or set(), parallel=parallel)
    finally:
        run.close()
    r = run.result
    r.seconds = time.monotonic() - t0
    return r


class _Run:
    def __init__(self, task: Task, sandbox: Sandbox | None, k: int, only: set[str] | None,
                 backend: str, static_only: bool, include_review: bool,
                 env_options: dict | None, fingerprints: dict[int, str],
                 on_findings: Callable[[list[Finding]], object] | None) -> None:
        self.task = task
        self.sandbox = sandbox
        self.owned = sandbox is None
        self.k = k
        self.only = only
        self.backend = backend
        self.quick = static_only
        self.include_review = include_review
        self.env_options = env_options
        self.fps = fingerprints
        self.on_findings = on_findings
        self.built = False
        self.baseline: Baseline | None = None
        self.ctx: ProbeContext | None = None
        self.result = AuditResult(task.id, baseline_sane=False, findings=[], checks=[])
        self.workers = 1
        self.depth = "fast"
        self.review: Session | None = None
        probes = discover()
        self.by_step: dict[Step, list[tuple[str, type]]] = {s: [] for s in STEPS}
        for pid, cls in probes.items():
            s = rules.step_of_probe(pid)
            if s is not None:
                self.by_step[s].append((pid, cls))

    # -- the loop --------------------------------------------------------------------

    def go(self, steps: set[int] | None, keep_going: bool, prior: AuditResult | None,
           reuse: set[int], parallel: bool = False) -> None:
        r = self.result
        later: list[Step] = []             # parallel: steps 3-6 to run together at the end
        for st in STEPS:
            n = st.number
            if r.stopped_at is not None:
                r.steps.append(StepRun(n, StepRun.NOT_RUN,
                                       reason=f"fix step {r.stopped_at} first"))
                continue
            if n in reuse and prior is not None:
                fs = self._take(prior, st)
            elif steps is not None and n not in steps:
                r.steps.append(StepRun(n, StepRun.SKIPPED, reason="not part of this run"))
                continue
            elif parallel and n >= 3:
                later.append(st)
                continue
            else:
                t = time.monotonic()
                ui.task_step(n, st.label)
                fs = self._step(st)
                r.steps.append(StepRun(n, StepRun.RAN, time.monotonic() - t,
                                       self.fps.get(n, "")))
            if step_failed(fs) and (not keep_going or (n <= 2 and (
                    foundation_failed(fs) or (self.baseline is not None
                                              and not self.baseline.sane)))):
                r.stopped_at = n
            elif n == 1:
                self._prefetch()
        if later:
            self._together(later)
        if self.baseline is not None:
            r.baseline = self.baseline
            r.baseline_sane = self.baseline.sane

    def _prefetch(self) -> None:
        """Once the build passed, start the L2 LLM calls in the background: they overlap
        with the baseline runs and the execution checks instead of waiting for their step.
        Results land in the session's memo, where the review checks (and the L3 leads) pick
        them up; a check that needs one still running waits for it."""
        s = self.review
        if s is None or not self.include_review or self.quick:
            return
        wanted = {pid for st in STEPS for pid, _ in self.by_step[st]
                  if pid.startswith(("review.", "agentic."))}
        if self.only:
            wanted &= set(self.only)
        if not wanted:
            return
        from rlgate.review import judges as J

        jobs = [(s.first, a) for a in J.ARTIFACTS if f"review.{a}" in wanted]
        jobs += [(s.second, q) for q in J.QUESTIONS if f"review.{q}" in wanted]
        if "agentic.strength" in wanted:
            jobs.append((s.second, "weak_pass"))
        if "agentic.ambiguity" in wanted:
            jobs.append((s.second, "determinability"))
        # the review reads the workspace as prepared (a staged backend pulls it out of the
        # image during the build); before that, only a plain on-disk workspace is safe
        task = getattr(self, "_prepared", None)
        if task is None:
            if self.task.workspace_env is not None:
                return
            task = self.task

        def run() -> None:
            from rlgate.variants.engine import pmap

            def one(job):
                fn, name = job
                try:
                    fn(task, name)
                except Exception:          # noqa: BLE001, S110 - the check reports it
                    pass
            pmap(one, list(dict.fromkeys(jobs)), 4)

        threading.Thread(target=run, name="rlgate-l2-prefetch", daemon=True).start()

    def _together(self, later: list[Step]) -> None:
        """Steps 3-6 at the same time (each in its own thread, checks in order inside it),
        with a live board of the steps; results are put back in step order afterwards."""
        from rlgate.variants.engine import pmap

        r = self.result
        if not self.quick and not self._ensure_baseline():    # the build broke after all
            for st in later:
                r.steps.append(StepRun(st.number, StepRun.NOT_RUN,
                                       reason=f"fix step {r.stopped_at} first"))
            return
        totals = {st.number: sum(1 for pid, _ in self.by_step[st]
                                 if not self.only or pid in self.only) for st in later}

        ui.task_step(later[0].number, "to " + str(later[-1].number) + " together")

        def one(st: Step) -> StepRun:
            ui.board_step(st.number)
            t = time.monotonic()
            failed = False
            try:
                failed = step_failed(self._step(st))
            finally:
                ui.board_done(st.number, failed)
            return StepRun(st.number, StepRun.RAN, time.monotonic() - t,
                           self.fps.get(st.number, ""))

        with ui.board([(st.number, st.label, totals[st.number]) for st in later]):
            r.steps.extend(pmap(one, later, len(later)))
        order = {s.number: i for i, s in enumerate(STEPS)}
        r.steps.sort(key=lambda sr: sr.number)
        step_of = {pid: st.number for st in STEPS for pid, _ in self.by_step[st]}
        r.checks.sort(key=lambda c: step_of.get(c.probe_id, 0))       # stable: order kept
        r.findings.sort(key=lambda f: order.get(getattr(rules.step_of(f.rule_id), "number",
                                                        0), 0))

    def _take(self, prior: AuditResult, st: Step) -> list[Finding]:
        """A step reused from the last run (its files didn't change)."""
        fs = [f for f in prior.findings if rules.step_of(f.rule_id) is st]
        pids = {pid for pid, _ in self.by_step[st]}
        self.result.findings.extend(fs)
        self.result.checks.extend(c for c in prior.checks if c.probe_id in pids)
        old = next((s for s in prior.steps if s.number == st.number), None)
        self.result.steps.append(StepRun(st.number, StepRun.CACHED,
                                         old.seconds if old else 0.0,
                                         self.fps.get(st.number, "")))
        if st is Step.BASELINE and prior.baseline is not None:
            self.baseline = prior.baseline
        if self.on_findings:
            self.on_findings(fs)
        return fs

    # -- one step ----------------------------------------------------------------------

    def _step(self, st: Step) -> list[Finding]:
        fs: list[Finding] = []
        if not self.quick:
            if st is Step.BUILD:
                fs += self._build()
                if step_failed(fs):        # nothing to run checks against
                    return self._done(fs, st, live_blocked=True)
            elif st is Step.BASELINE:
                if not self._ensure_built():
                    return self._done(fs, st, live_blocked=True)
                from rlgate.baseline.invariant import has_reference
                what = (f"Run the reference solution and a do-nothing submission, {self.k} "
                        "times each" if has_reference(self._prepared, self._sandbox()) else
                        f"Run a do-nothing submission {self.k} times (no reference solution)")
                with ui.step(what):
                    self.baseline = establish(self._prepared, self._sandbox(), k=self.k,
                                             workers=self.workers)
                fs += baseline_findings(self._prepared, self.baseline)
                self._ensure_baseline()           # the context step 2's own probes use
            elif not self._ensure_baseline():
                return self._done(fs, st, live_blocked=True)
        fs += self._probes(st)
        return self._done(fs, st)

    def _done(self, fs: list[Finding], st: Step, live_blocked: bool = False) -> list[Finding]:
        if live_blocked:                  # list the step's checks as blocked, not missing
            for pid, cls in self.by_step[st]:
                if self.only and pid not in self.only:
                    continue
                if not any(c.probe_id == pid for c in self.result.checks):
                    self.result.checks.append(CheckRun(pid, cls.meta.family,
                                                       CheckRun.SKIPPED_BASELINE,
                                                       doc=cls.meta.doc))
        for f in fs:
            if not f.rule_id:
                _with_rules([f])
        if self.on_findings:
            self.on_findings(fs)
        self.result.findings.extend(fs)
        return fs

    def _probes(self, st: Step) -> list[Finding]:
        out: list[Finding] = []
        for pid, cls in self.by_step[st]:
            if self.only and pid not in self.only:
                continue
            try:
                out.extend(self._probe(pid, cls))
            finally:
                ui.board_tick()
        return out

    def _probe(self, pid: str, cls: type) -> list[Finding]:
        probe = cls()
        m = probe.meta
        if not probe.applicable(self.task if self.ctx is None else self._prepared):
            self.result.checks.append(CheckRun(pid, m.family, CheckRun.NA, doc=m.doc))
            return []
        if m.needs_llm and self.review is None:
            self.result.checks.append(CheckRun(pid, m.family, CheckRun.SKIPPED_DEEP,
                                               doc=m.doc))
            return []
        if m.needs_live and self.quick:
            self.result.checks.append(CheckRun(pid, m.family, CheckRun.SKIPPED_LIVE,
                                               doc=m.doc))
            return []
        ctx = self.ctx if self.ctx is not None else _static_ctx()
        ctx.review = self.review
        task = self._prepared if self.ctx is not None else self.task
        t = time.monotonic()
        try:
            if self.quick:
                fs = _kept(_with_rules(probe.run(task, ctx)), self.include_review)
            else:
                with ui.step(check_name(pid)):
                    fs = _kept(_with_rules(probe.run(task, ctx)), self.include_review)
        except NotApplicable as e:
            self.result.checks.append(CheckRun(pid, m.family, CheckRun.NA, doc=m.doc,
                                               seconds=time.monotonic() - t,
                                               reason=str(e)))
            return []
        except CouldNotJudge as e:
            self.result.checks.append(CheckRun(pid, m.family, CheckRun.UNFINISHED, doc=m.doc,
                                               seconds=time.monotonic() - t,
                                               reason=str(e)))
            return []
        self.result.checks.append(CheckRun(
            pid, m.family, CheckRun.FLAGGED if fs else CheckRun.GOOD, len(fs), m.doc,
            seconds=time.monotonic() - t, note=getattr(probe, "note", "")))
        return fs

    # -- environment -------------------------------------------------------------------

    def _sandbox(self) -> Sandbox:
        if self.sandbox is None:
            self.sandbox = build_sandbox(self.task, prefer=self.backend,
                                         options=self.env_options)
        return self.sandbox

    def _build(self) -> list[Finding]:
        """Step 1's live part: build every image the task uses. A failed build is the
        task's defect (RG608), reported per image."""
        try:
            with ui.step("Build every image the task uses (cached after the first run)"):
                self._sandbox().build()
        except BuildFailed as e:
            return [_build_finding(what, log) for what, log in e.failures]
        self.built = True
        self._prepared = prepare(self.task, self._sandbox())
        return []

    def _ensure_built(self) -> bool:
        """Steps after 1 need the environment even when step 1 came from the cache."""
        if self.built:
            return True
        fs = self._build()
        if fs:                            # it no longer builds: step 1 fails after all
            self._done(fs, Step.BUILD)
            self.result.stopped_at = 1
            for s in self.result.steps:
                if s.number == 1:
                    s.state = StepRun.RAN
            return False
        return True

    def _ensure_baseline(self) -> bool:
        if not self._ensure_built():
            return False
        if self.baseline is None:         # e.g. --only 4 with no saved step 2
            with ui.step("Run the reference solution and a do-nothing submission first, "
                         f"{self.k} times each (later steps need it)"):
                self.baseline = establish(self._prepared, self._sandbox(), k=self.k,
                                             workers=self.workers)
        if self.ctx is None:
            self.ctx = ProbeContext(sandbox=self._sandbox(), baseline=self.baseline,
                                    config={"workers": self.workers, "depth": self.depth},
                                    variants=VariantEngine(self._prepared, self._sandbox(),
                                                           self.baseline,
                                                           workers=self.workers,
                                                           depth=self.depth))
        return True

    _prepared: Task

    def close(self) -> None:
        if self.owned and self.sandbox is not None:
            self.sandbox.close()   # delete remote machines now, not at auto-stop


def _static_ctx() -> ProbeContext:
    return ProbeContext(sandbox=None, baseline=Baseline(0.0, 0.0, 0),  # type: ignore[arg-type]
                        variants=None)  # type: ignore[arg-type]


def _build_finding(what: str, log: str) -> Finding:
    from rlgate.core.finding import Evidence
    rule = rules.get("RG608")
    tail = "\n".join(log.strip().splitlines()[-12:])
    return Finding(
        probe_id="baseline.build", rule_id="RG608", title=rule.title,
        severity=rule.severity, verdict=VerdictTier.CONFIRMED, location=what,
        evidence=Evidence(kind="reproduced_exploit",
                          detail=f"{what} failed to build:\n{tail}" if tail else
                          f"{what} failed to build"),
        fix_hint=rule.fix)


def prepare(task: Task, sandbox: Sandbox) -> Task:
    """After ``build``: a sandbox that pulled the workspace out of an image hands back the
    real files (they replace the on-disk build context as ``repo_dir``), and a task whose
    oracle is a solution SCRIPT gets its gold patch derived from running it."""
    from dataclasses import replace

    from rlgate.core.task import Patch

    ws = getattr(sandbox, "workspace_dir", None)
    if ws is not None:
        task = replace(task, repo_dir=ws)
    if task.gold_patch is None and task.oracle is not None:
        derive = getattr(sandbox, "derive_gold", None)
        if derive is not None:
            text = derive()
            if text:
                task = replace(task, gold_patch=Patch(text),
                               meta={**task.meta, "gold_patch": "derived from solution script"})
    return task


# What each check is called in progress lines and --verbose output (plain language).
CHECK_NAMES = {
    "grader_strength.revert_probe": "Remove each part of the fix; the grader must fail",
    "grader_strength.mutation_probe": "Make small changes to the fix; the grader must notice",
    "grader_strength.stub_probe": "Replace each changed function with a stub; the grader must "
                                  "fail",
    "grader_strength.hardcode_probe": "Hardcode the answers to the visible tests; the graded "
                                      "tests must fail it",
    "forgery.conftest_injection": "Plant a conftest.py that forces a pass",
    "forgery.exit_code": "Exit early with code 0; the grader must fail",
    "forgery.report_forgery": "Write a fake passing test report; the grader must fail",
    "forgery.equality_override": "Return objects equal to anything; the grader must fail",
    "forgery.env_hijack": "Plant sitecustomize, import hooks and a patched pytest; the grader "
                          "must fail",
    "forgery.timer_tampering": "Fake the clock on a timed task; the grader must fail",
    "forgery.test_selection": "Change which tests run (config, skip and xfail marks); the "
                              "grader must fail",
    "forgery.verifier_command": "Check the grading command can't fake success or hide failures",
    "forgery.test_rewrite": "Rewrite the graded tests to do nothing; the grader must fail",
    "forgery.forgeable_report": "Look for a test report the agent could write",
    "forgery.unrestored_test_infra": "Look for test setup files the agent could edit",
    "forgery.world_readable_tests": "Check whether the agent can read the held-out tests",
    "forgery.mutable_test_data": "Look for test data the agent could change",
    "leakage.git_unreachable_objects": "Search git history for the fix",
    "leakage.cache_scan": "Search caches and patch leftovers for the answer",
    "leakage.mtime_clustering": "Look for files added after checkout",
    "leakage.agent_env_scan": "Search the agent's environment for solution and test files",
    "leakage.open_internet": "Check the agent's network access",
    "test_quality.overfit_equivalence": "Reword incidental strings; tests must still pass",
    "test_quality.symbol_crossref": "Look for tests that use names the task never mentions",
    "test_quality.brittle_assertions": "Look for tests pinned to exact wording or call counts",
    "reward_shape.conformance": "Check how the reward is wired (seven checks)",
    "hygiene.dockerfile": "Check every Dockerfile for reproducibility and layer order",
    "hygiene.grader_downloads": "Check the grader doesn't download software while grading",
    "hygiene.verifier_isolation": "Check the grader runs apart from the agent",
    "hygiene.carryover": "Leave files and a process behind; the grader must not see them",
    "hygiene.limits": "Check the task declares its CPU, memory and time limits",
    "hygiene.harbor_compat": "Check the task runs the way its harness runs it",
    "review.statement": "LLM review: is the statement actionable, leak-free, self-contained",
    "review.tests": "LLM review: do the tests match the statement and accept equivalents",
    "review.reference": "LLM review: is the reference fix on-task, with nothing hardcoded",
    "review.verifier": "LLM review: can the verifier command pass without the tests",
    "review.determinability": "LLM review, reference hidden: can every tested requirement "
                              "be known",
    "review.enforcement": "LLM review, reference hidden: do the tests enforce the request",
    "review.weak_pass": "LLM review, reference hidden: could a trivial solution pass",
    "agentic.strength": "Agent writes wrong or partial implementations; the grader must fail "
                        "them",
    "agentic.ambiguity": "Agent writes a correct alternative the task allows; the grader "
                         "must pass it",
    "agentic.hack": "Hack agent tries to reach full reward without solving the task",
    "agentic.rubric": "Attackers write cheap deliverables; rubric and goal judges grade them",
    "rubric.static": "Check rubric criteria are atomic, observable, distinct and weighted",
}


def check_name(probe_id: str) -> str:
    return CHECK_NAMES.get(probe_id, probe_id)


def _with_rules(findings: list[Finding]) -> list[Finding]:
    """Give every finding its rule ID: single-rule probes get the catalogue default;
    multi-rule probes must set it themselves (a missing one is a bug, so fail loudly)."""
    for f in findings:
        if not f.rule_id:
            rid = rules.default_rule(f.probe_id)
            if rid is None:
                raise ValueError(f"{f.probe_id} emitted a finding without a rule_id and has no "
                                 "single default rule in rlgate/core/rules.py")
            f.rule_id = rid
        rules.get(f.rule_id)  # unknown IDs fail here, not in a report
    return findings
