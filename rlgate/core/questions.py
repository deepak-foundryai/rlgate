"""From a run to what the user reads: the six numbered steps, one verdict, the counts.

Checks are grouped by the step they belong to (``rules.Step``), in run order. Each step gets
one status:

  passed          ✓  every check in it ran clean, and the row says what was verified
  failed          ✗  an open proven problem; the run stopped here (-x, or a broken build or baseline)
  to review       ?  a real signal a person must decide on (closed by a fix or a waiver)
  accepted        ✓  only waived findings left; shown, never hidden
  partly checked  ✓  a quick run: the file-only part ran clean, the rest needs a full run
  not run         -  an earlier step failed ("fix step N first"), or it wasn't asked for
  skipped         -  nothing in it could run or apply; the row says why

Warnings (build hygiene, low severity) never change a step's status; they are counted on its
row ("1 warning: base image not pinned").

Verdict (user-facing labels; internal KEEP/FIX/DROP):
  Broken       a critical proven problem: the gold fails, doing nothing passes, the grade is
               flaky, an image doesn't build, the answer is in the agent's environment
  Needs fixes  any open proven problem or open judgment item
  Ready        all six steps passed with nothing open (warnings and waivers allowed)
A run that didn't cover all six steps (quick, --only, stopped early) is not ``complete``:
with nothing found it says so instead of claiming Ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rlgate.core import rules
from rlgate.core.finding import Finding, Severity, VerdictTier
from rlgate.core.rules import STEPS, Proof, Step

PASSED, PROVEN, JUDGMENT, WARNING, ACCEPTED, SKIPPED, NOT_YET = (
    "passed", "proven", "judgment", "warning", "accepted", "skipped", "not-yet")
NOT_RUN, PARTLY = "not-run", "partly"

READY, NEEDS_FIXES, BROKEN = "Ready", "Needs fixes", "Broken"
# the task was never examined: the backend (Daytona, Docker) failed, not the task. Says
# nothing about the task; rerunning without --fresh redoes only these.
NOT_CHECKED = "Not checked"

# the word shown next to each step's symbol
STATUS_WORD = {PASSED: "passed", PROVEN: "failed", JUDGMENT: "to review",
               ACCEPTED: "accepted", PARTLY: "partly checked", NOT_RUN: "not run",
               SKIPPED: "skipped", WARNING: "passed", NOT_YET: "not checked yet"}

# names for --only / [checks] skip: the step names, plus the earlier question names
STEP_SLUGS: dict[str, Step] = {s.slug: s for s in STEPS}
_ALIASES = {"works": Step.BASELINE, "leaked": Step.LEAKS, "reward": Step.GRADER,
            "faked": Step.CHEATING, "fair": Step.FAIRNESS, "build": Step.BUILD,
            "grader": Step.GRADER}


def resolve_group(name: str) -> Step:
    """``grader`` / ``4`` / ``RG1`` / ``RG101`` / "Can a pass be faked?" → the step."""
    n = name.strip().lower()
    if n in STEP_SLUGS:
        return STEP_SLUGS[n]
    if n in _ALIASES:
        return _ALIASES[n]
    if n.isdigit() and 1 <= int(n) <= len(STEPS):
        return Step(int(n))
    if n.upper().startswith("RG") and len(n) >= 3:
        s = rules.step_of(n.upper())
        if s is not None:
            return s
    for s in STEPS:
        if s.text.lower().rstrip("?") == n.rstrip("?"):
            return s
    raise ValueError(f"unknown step {name!r}; use a number 1-6 or one of: "
                     f"{', '.join(STEP_SLUGS)}")


def probes_in(step: Step, probe_ids) -> set[str]:
    """Probes whose rules belong to ``step``."""
    return {pid for pid in probe_ids if rules.step_of_probe(pid) is step}


def step_of_probe(probe_id: str) -> Step | None:
    return rules.step_of_probe(probe_id)


def is_warning(f: Finding) -> bool:
    """Build hygiene judgment calls and low-severity items: listed, never blocking."""
    rule = rules.RULES.get(f.rule_id)
    if rule is not None and rule.step is Step.BUILD and rule.proof is Proof.JUDGMENT:
        return True
    return f.severity <= Severity.LOW


def kind_of(f: Finding) -> str:
    """proven / judgment / warning / accepted for one finding."""
    if getattr(f, "waiver", None):
        return ACCEPTED
    if is_warning(f):
        return WARNING
    return PROVEN if f.verdict is VerdictTier.CONFIRMED else JUDGMENT


@dataclass
class StepRow:
    step: Step
    status: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    seconds: float = 0.0
    cached: bool = False

    @property
    def word(self) -> str:
        return STATUS_WORD[self.status]


@dataclass
class Outcome:
    verdict: str
    rows: list[StepRow]
    proven: int = 0
    judgment: int = 0
    warnings: int = 0
    accepted: int = 0
    stopped_at: int | None = None
    complete: bool = True           # all six steps ran (or came from the cache)

    @property
    def exit_code(self) -> int:
        return {READY: 0, NEEDS_FIXES: 1}.get(self.verdict, 2)

    def counts_line(self) -> str:
        parts = []
        if self.proven:
            parts.append(_n(self.proven, "proven problem"))
        if self.judgment:
            parts.append(f"{self.judgment} to review")
        if self.warnings:
            parts.append(_n(self.warnings, "warning"))
        if self.accepted:
            parts.append(f"{self.accepted} accepted")
        return " · ".join(parts) or "no problems found"

    def headline(self) -> str:
        """``STOPPED AT STEP 4 of 6 · NEEDS FIXES`` / ``ALL 6 STEPS PASSED · READY``."""
        n = len(STEPS)
        if self.stopped_at is not None:
            return f"STOPPED AT STEP {self.stopped_at} of {n} · {self.verdict.upper()}"
        if self.complete:
            if self.verdict == READY:
                return f"ALL {n} STEPS PASSED · {self.verdict.upper()}"
            bad = [r.step.number for r in self.rows if r.status == PROVEN]
            which = (f" · STEP{'S' if len(bad) > 1 else ''} {', '.join(map(str, bad))} FAILED"
                     if bad else "")
            return f"ALL {n} STEPS RUN{which} · {self.verdict.upper()}"
        if self.verdict == READY:
            return "NOT FULLY CHECKED · NO PROBLEMS FOUND SO FAR"
        return f"NOT FULLY CHECKED · {self.verdict.upper()}"

    def top(self) -> Finding | None:
        """The finding to look at first (drives the "Next:" command)."""
        order = {PROVEN: 0, JUDGMENT: 1, WARNING: 2}
        open_ = [f for r in self.rows for f in r.findings if kind_of(f) in order]
        if not open_:
            return None
        return min(open_, key=lambda f: (order[kind_of(f)], r_step(f), -int(f.severity),
                                         f.rule_id))


def r_step(f: Finding) -> int:
    s = rules.step_of(f.rule_id)
    return s.number if s else 99


def outcome(result, *, quick: bool, full_possible: bool = True,
            selected: set[Step] | None = None, why_no_full: str | None = None,
            skipped_in_config: set[Step] | None = None) -> Outcome:
    """Group one task's ``AuditResult`` into the six step rows + the verdict."""
    from rlgate.pipeline import StepRun

    by_step: dict[Step, list[Finding]] = {s: [] for s in STEPS}
    for f in result.findings:
        s = rules.step_of(f.rule_id)
        if s is not None:
            by_step[s].append(f)
    checks_by_step: dict[Step, list] = {s: [] for s in STEPS}
    for c in result.checks:
        s = step_of_probe(c.probe_id)
        if s is not None:
            checks_by_step[s].append(c)
    runs = {sr.number: sr for sr in getattr(result, "steps", [])}

    rows: list[StepRow] = []
    for s in STEPS:
        sr = runs.get(s.number)
        fs = by_step[s]
        if sr is not None and sr.state == StepRun.NOT_RUN:
            rows.append(StepRow(s, NOT_RUN, sr.reason))
            continue
        if (sr is not None and sr.state == StepRun.SKIPPED) or (
                sr is None and selected is not None and s not in selected and not fs):
            why = "skipped in rlgate.toml [checks]" if skipped_in_config and \
                s in skipped_in_config else "not part of this run"
            rows.append(StepRow(s, NOT_RUN, why))
            continue
        row = _row(s, checks_by_step[s], fs, result, quick, full_possible, why_no_full)
        if sr is not None:
            row.seconds = sr.seconds or row.seconds
            row.cached = sr.state == StepRun.CACHED
        rows.append(row)

    kinds = [kind_of(f) for f in result.findings]
    proven = kinds.count(PROVEN)
    judgment = kinds.count(JUDGMENT)
    # Broken: the task can't be used as it stands (gold fails, doing nothing passes, a flaky
    # grade, an image that doesn't build, a leaked answer): a critical proven problem
    critical = any(kind_of(f) == PROVEN and f.severity >= Severity.CRITICAL
                   for f in result.findings)
    verdict = BROKEN if critical else NEEDS_FIXES if (proven or judgment) else READY
    complete = all(r.status in (PASSED, PROVEN, JUDGMENT, ACCEPTED) for r in rows)
    return Outcome(verdict, rows, proven, judgment, kinds.count(WARNING),
                   kinds.count(ACCEPTED), getattr(result, "stopped_at", None), complete)


def _row(s: Step, checks, fs: list[Finding], result, quick: bool, full_possible: bool,
         why_no_full: str | None) -> StepRow:
    from rlgate.pipeline import CheckRun

    secs = sum(getattr(c, "seconds", 0.0) for c in checks)
    kinds = [kind_of(f) for f in fs]
    warns = [f for f in fs if kind_of(f) == WARNING]
    ran = [c for c in checks if c.status in (CheckRun.GOOD, CheckRun.FLAGGED)]
    not_run = [c for c in checks if c.status == CheckRun.SKIPPED_LIVE]
    unfinished = [c for c in checks if c.status == CheckRun.UNFINISHED]
    blocked = [c for c in checks if c.status == CheckRun.SKIPPED_BASELINE]
    live_part = s in (Step.BUILD, Step.BASELINE)     # the build / the graded runs
    b = result.baseline

    if PROVEN in kinds:
        shown = [f for f in fs if kind_of(f) == PROVEN]
        summary = _plain(shown[0]) + (f" (+{len(shown) - 1} more)" if len(shown) > 1 else "")
        return StepRow(s, PROVEN, summary, fs, secs)
    if JUDGMENT in kinds:
        n = kinds.count(JUDGMENT)
        summary = "1 item needs your judgment" if n == 1 else f"{n} items need your judgment"
        return StepRow(s, JUDGMENT, summary + _warn_note(warns), fs, secs)

    partly = quick and (live_part or bool(not_run))
    if s is Step.BASELINE and not quick and b is not None:
        verified = _baseline_summary(b)
    elif s is Step.BUILD and not quick and not blocked:
        verified = "task builds"
    elif ran:
        clean = [c for c in ran if c.status == CheckRun.GOOD]
        verified = _n(sum(7 if c.probe_id == "reward_shape.conformance" else 1
                          for c in clean), "check") + " clean" if clean else ""
    else:
        verified = ""

    if unfinished:          # fail closed: an LLM check without a verdict is not a pass
        lead = verified or "other checks clean"
        why = unfinished[0].reason or "no verdict"
        return StepRow(s, PARTLY, f"{lead}; the LLM review didn't finish: {why}"
                       + _warn_note(warns), fs, secs)
    if ACCEPTED in kinds and not ran and not verified:
        n = kinds.count(ACCEPTED)
        return StepRow(s, ACCEPTED, _n(n, "finding") + " accepted, with reason", fs, secs)
    if partly:
        if not verified and not warns:
            return StepRow(s, SKIPPED, _skip_reason(quick, full_possible, why_no_full), fs,
                           secs)
        rest = "the rest needs a full run" if full_possible and not why_no_full \
            else f"the rest needs a full run ({why_no_full or 'needs Docker or Daytona'})"
        lead = verified or "file checks clean"
        return StepRow(s, PARTLY, f"{lead}; {rest}" + _warn_note(warns), fs, secs)
    if blocked and not ran and not verified:
        return StepRow(s, SKIPPED, "could not run: the environment didn't build", fs, secs)
    if not verified:
        if warns:
            return StepRow(s, PASSED, _warn_note(warns).strip(" ()"), fs, secs)
        return StepRow(s, SKIPPED, "doesn't apply to this task", fs, secs)
    if ACCEPTED in kinds:
        verified += f", {kinds.count(ACCEPTED)} accepted with reason"
    return StepRow(s, PASSED, verified + _warn_note(warns), fs, secs)


def _baseline_summary(b) -> str:
    k = len(b.oracle_runs) or b.k
    if not getattr(b, "reference", True):
        return f"no reference solution; do-nothing fails {b.nop_fails}/{len(b.nop_runs) or k}"
    out = f"gold passes {b.gold_passes}/{k}, do-nothing fails {b.nop_fails}/{k}"
    if getattr(b, "reports", False) and b.per_test:
        out += f"; {len(b.fail_to_pass)} fail-to-pass, {len(b.pass_to_pass)} pass-to-pass tests"
    return out


def _warn_note(warns: list[Finding]) -> str:
    if not warns:
        return ""
    if len(warns) == 1:
        t = _plain(warns[0])
        return f" (1 warning: {t[:1].lower() + t[1:]})"
    return f" ({len(warns)} warnings)"


def _skip_reason(quick: bool, full_possible: bool, why_no_full: str | None = None) -> str:
    if why_no_full:
        return f"needs a full run, which rlgate can't do here: {why_no_full}"
    if quick and full_possible:
        return "needs a full run: run without --quick"
    return "needs Docker or Daytona: run without --quick on a machine that has one"


def _plain(f: Finding) -> str:
    rule = rules.RULES.get(f.rule_id)
    title = rule.title if rule else f.title
    if f.title and rule and f.title != rule.title:
        return f.title
    return title


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# -- the three reference-hidden review questions (L2 second pass) ------------------------
#
# One answer each, worded so that Yes is good. A No is a valid negative from any one vote
# (needs your judgment), or a proof from the L3 agent on a full run (RG408 / RG110).

QUESTIONS = (
    ("review.determinability",
     ("Can every tested requirement be known from what the solver sees?"),
     ("RG704", "RG408")),
    ("review.enforcement", "Do the tests enforce the requested behaviour?", ("RG108",)),
    ("review.weak_pass", "Does the grader reject trivial or incomplete solutions?",
     ("RG109", "RG110")),
)
CHECK_SETS = {"questions": frozenset(q[0] for q in QUESTIONS)}
YES, NO, NO_PROVEN, UNFINISHED, NOT_APPLICABLE, NOT_ASKED = (
    "yes", "no", "no-proven", "unfinished", "n/a", "not-asked")


@dataclass
class Answer:
    question: str
    answer: str                   # one of YES, NO, NO_PROVEN, UNFINISHED, ...
    detail: str = ""              # the first problem found, or why there is no answer
    findings: list[Finding] = field(default_factory=list)
    votes: str = ""               # "3 calls (openai/…, gemini/…)"


def answers(result) -> list[Answer] | None:
    """The three answers for a run, or None when none of the questions was asked (no
    --deep-review, or not selected)."""
    from rlgate.pipeline import CheckRun

    if result is None:
        return None
    checks = {c.probe_id: c for c in result.checks}
    asked = [checks.get(pid) for pid, _, _ in QUESTIONS]
    if all(c is None or c.status in (CheckRun.SKIPPED_DEEP, CheckRun.SKIPPED_BASELINE,
                                     CheckRun.SKIPPED_LIVE) for c in asked):
        return None
    out = []
    for (pid, text, rids), c in zip(QUESTIONS, asked, strict=True):
        open_ = [f for f in result.findings if f.rule_id in rids and not f.waiver]
        proven = [f for f in open_ if f.verdict is VerdictTier.CONFIRMED]
        votes = c.note.split(";")[0] if c is not None and c.note else ""
        if proven:
            out.append(Answer(text, NO_PROVEN, _problem(proven), proven, votes))
        elif open_:
            out.append(Answer(text, NO, _problem(open_), open_, votes))
        elif c is None or c.status in (CheckRun.SKIPPED_DEEP, CheckRun.SKIPPED_BASELINE,
                                       CheckRun.SKIPPED_LIVE):
            out.append(Answer(text, NOT_ASKED, c.reason if c is not None else ""))
        elif c.status == CheckRun.UNFINISHED:
            out.append(Answer(text, UNFINISHED, c.reason or "no verdict", votes=votes))
        elif c.status == CheckRun.NA:
            out.append(Answer(text, NOT_APPLICABLE, c.reason or "doesn't apply"))
        else:
            waived = sum(1 for f in result.findings if f.rule_id in rids and f.waiver)
            out.append(Answer(text, YES, (f"{waived} accepted with a reason" if waived
                                          else "no judge found a problem"), votes=votes))
    return out


def _problem(fs: list[Finding]) -> str:
    f = fs[0]
    more = f" (+{len(fs) - 1} more)" if len(fs) > 1 else ""
    return f"{f.rule_id} at {f.location}: {f.title}{more}"
