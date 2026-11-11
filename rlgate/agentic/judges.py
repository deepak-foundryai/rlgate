"""The two L3 judges: the verifier checked against the spec in both directions.

**Verifier strength** (is it too weak?): the agent sees what a solver sees — the statement
and the pre-change repository, never the tests or the reference. It first records every
independently testable requirement, each with a verbatim quote, and the list is frozen once
grading starts, so every bypass comes from solver-visible information. It then writes
incomplete or wrong implementations and grades them. A bypass is **proven** when:

  1. the verifier passes the candidate;
  2. the agent's own check of the requirement (a shell command, exit 0 = met) FAILS on the
     candidate — the candidate really breaks the requirement;
  3. the same check PASSES on the reference fix — the check tests what the task requires.

**Ambiguity** (is it too strict?): the agent may read the tests (explicitly allowed here) but
not the reference. For each test requirement it looks for a justification in the statement,
searching the repository's code, docs and interfaces before flagging anything. An
alternative is **proven** rejected when:

  1. its requirement quote is in the statement and its assertion quote is in the tests;
  2. the verifier fails it, and (with a per-test report) only on the tests the agent named,
     every other graded test passing — so it is otherwise complete;
  3. the agent's check of the stated requirement passes on the alternative AND on the
     reference fix — by the statement, both are correct.

L2 leads come in with ids: weak-pass shortcuts are tried as bypasses; determinability gaps
are either resolved from repository context (a verbatim quote from a repo file) or kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rlgate.agentic.bench import Bench, ToolError, dispatch, fn
from rlgate.review.cite import names_a_test, quoted

S = {"type": "string"}


@dataclass
class Requirement:
    id: str
    text: str
    quote: str
    source: str                  # "statement" or a repository file


@dataclass
class Bypass:
    requirement: Requirement
    how: str
    check: str
    patch: str
    reward: float
    lead: str = ""


@dataclass
class Alternative:
    requirement: str             # the statement's words it follows
    test: str
    assertion: str
    why: str
    check: str
    patch: str
    failed: list[str]            # tests the verifier failed it on ([] without a report)
    reward: float
    lead: str = ""


@dataclass
class Audit:
    """What one L3 audit did and proved."""
    finished: bool = False
    summary: str = ""
    turns: int = 0
    grades: int = 0
    requirements: list[Requirement] = field(default_factory=list)
    bypasses: list[Bypass] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    leads: dict[str, str] = field(default_factory=dict)    # lead → confirmed | rejected |
                                                           # resolved | kept
    resolved: dict[str, str] = field(default_factory=dict)  # lead → "file: quote"
    log: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        bits = [f"{self.turns} agent turns", f"{self.grades} implementation(s) graded"]
        if self.requirements:
            bits.insert(0, f"{len(self.requirements)} requirements recorded")
        if not self.finished:
            bits.append("stopped at its limit")
        return "; ".join(bits)


class _Judge:
    """Shared by both judges: the bench, the grading budget, ``finish``."""

    def __init__(self, bench: Bench, leads: list[tuple[str, str]], max_grades: int) -> None:
        self.bench = bench
        self.leads = leads                   # (id, what L2 said)
        self.max_grades = max_grades
        self.audit = Audit()
        for lid, _ in leads:
            self.audit.leads[lid] = "kept"

    @property
    def done(self) -> bool:
        return self.audit.finished

    def specs(self) -> list[dict]:
        return self.bench.specs() + self.own_specs() + [
            fn("finish", "End the audit with a short summary of what you checked.",
               {"summary": S})]

    def own_specs(self) -> list[dict]:
        return []

    def call(self, name: str, raw: str | dict) -> str:
        if hasattr(self, f"t_{name}"):
            return dispatch(self, name, raw)
        return self.bench.call(name, raw)

    def t_finish(self, summary: str = "") -> str:
        self.audit.finished = True
        self.audit.summary = summary
        return "audit finished"

    def _spend(self) -> None:
        if self.audit.grades >= self.max_grades:
            raise ToolError(f"the {self.max_grades} gradings are used up; call finish")
        if not self.bench.files:
            raise ToolError("your candidate is empty: write the implementation first")
        self.audit.grades += 1

    def _lead(self, lead: str) -> str:
        lead = (lead or "").strip()
        if lead and lead not in self.audit.leads:
            raise ToolError(f"{lead!r} is not one of the leads "
                            f"({', '.join(self.audit.leads) or 'none'})")
        return lead

    def _check_both(self, patch: str, check: str) -> tuple[int | None, int | None]:
        """Exit codes of ``check`` on the candidate and on the reference fix (None: that
        patch didn't apply)."""
        mine = self.bench.execute(patch, check)
        gold = self.bench.task.gold_patch
        ref = self.bench.execute(gold.text if gold else None, check)
        return (None if mine is None else mine.exit_code,
                None if ref is None else ref.exit_code)


# -- verifier strength ----------------------------------------------------------------------

class StrengthJudge(_Judge):
    def own_specs(self) -> list[dict]:
        req = {"type": "object", "properties": {
            "id": S, "text": S, "quote": S,
            "source": {"type": "string", "description": "statement, or the repository file "
                       "the quote is from"}},
            "required": ["id", "text", "quote", "source"]}
        return [
            fn("record_requirements", "Record the independently testable requirements, each "
               "with a verbatim quote from the statement or a repository file. Allowed only "
               "before your first grading.",
               {"requirements": {"type": "array", "items": req}}),
            fn("try_bypass", "Grade your current candidate against the real verifier. It "
               "must break requirement_id. check_cmd is a shell command that exits 0 when "
               "the requirement is met (it runs on your candidate and on the reference fix).",
               {"requirement_id": S, "how": {"type": "string", "description": "what your "
                "candidate does wrong or leaves out"}, "check_cmd": S,
                "lead": {"type": "string", "description": "the L2 lead this tries, if any"}},
               ["requirement_id", "how", "check_cmd"]),
        ]

    def t_record_requirements(self, requirements: list) -> str:
        if self.audit.grades:
            raise ToolError("requirements are fixed once grading starts, so every bypass "
                            "comes from what a solver can see")
        ok, bad = [], []
        for r in requirements:
            try:
                req = Requirement(str(r["id"]).strip(), str(r["text"]), str(r["quote"]),
                                  str(r.get("source") or "statement"))
            except (KeyError, TypeError):
                bad.append(f"{r!r}: needs id, text, quote and source")
                continue
            if not self._quoted(req):
                bad.append(f"{req.id}: the quote is not verbatim in {req.source}")
                continue
            ok.append(req)
        known = {r.id: r for r in self.audit.requirements}
        known.update({r.id: r for r in ok})
        self.audit.requirements = list(known.values())
        msg = f"recorded {len(ok)} requirement(s): {', '.join(r.id for r in ok) or '-'}"
        return msg + ("\nnot recorded:\n" + "\n".join(bad) if bad else "")

    def _quoted(self, r: Requirement) -> bool:
        if r.source.strip().lower() == "statement":
            return quoted(r.quote, self.bench.task.instruction or "")
        try:
            return quoted(r.quote, self.bench._text(self.bench._path(r.source)))
        except ToolError:
            return False

    def t_try_bypass(self, requirement_id: str, how: str, check_cmd: str,
                     lead: str = "") -> str:
        req = next((r for r in self.audit.requirements if r.id == requirement_id), None)
        if req is None:
            raise ToolError(f"{requirement_id!r} is not a recorded requirement "
                            "(record_requirements first)")
        lead = self._lead(lead)
        self._spend()
        patch = self.bench.patch()
        g = self.bench.grade(patch)
        if g is None:
            return "your candidate doesn't apply to the workspace; show_changes"
        if not g.passed:
            if lead and self.audit.leads[lead] != "confirmed":
                self.audit.leads[lead] = "rejected"
            self.audit.log.append(f"rejected: {how}")
            return f"The verifier rejected it (reward {g.reward}). It catches this."
        mine, ref = self._check_both(patch, check_cmd)
        if mine is None or ref is None:
            return "The verifier accepted it, but the check couldn't run on both versions."
        if mine == 0:
            return ("The verifier accepted it, but your check passes on it, so it doesn't "
                    "show the requirement broken. Tighten the check or the candidate.")
        if ref != 0:
            return ("The verifier accepted it, but your check also fails on the reference "
                    "fix, so it tests something the task doesn't require. Fix the check.")
        self.audit.bypasses.append(Bypass(req, how, check_cmd, patch, g.reward, lead))
        if lead:
            self.audit.leads[lead] = "confirmed"
        return (f"Bypass confirmed: the verifier passes it (reward {g.reward}), your check "
                f"shows {req.id} broken, and the reference meets it.")


# -- ambiguity --------------------------------------------------------------------------------

class AmbiguityJudge(_Judge):
    def __init__(self, bench: Bench, leads: list[tuple[str, str]], max_grades: int,
                 baseline_tests: dict[str, bool]) -> None:
        super().__init__(bench, leads, max_grades)
        self.gold_passing = sorted(t for t, ok in baseline_tests.items() if ok)

    def own_specs(self) -> list[dict]:
        return [
            fn("resolve_lead", "Settle an L2 lead: resolved=true when repository context "
               "(code, docs, an existing interface) tells a solver the tested detail; give "
               "that file and a verbatim quote from it.",
               {"lead": S, "resolved": {"type": "boolean"}, "file": S, "quote": S,
                "reason": S}),
            fn("try_alternative", "Grade your current candidate: a complete implementation "
               "that follows the statement but makes a different, reasonable choice where "
               "the statement leaves one open. check_cmd exits 0 when the stated requirement "
               "is met (it runs on your candidate and on the reference fix).",
               {"requirement_quote": {"type": "string", "description": "verbatim from the "
                "statement"}, "test": S, "assertion_quote": {"type": "string",
                "description": "verbatim from the test"}, "why_valid": S, "check_cmd": S,
                "expected_failures": {"type": "array", "items": S},
                "lead": S},
               ["requirement_quote", "test", "assertion_quote", "why_valid", "check_cmd",
                "expected_failures"]),
        ]

    def t_resolve_lead(self, lead: str, resolved: bool, file: str = "", quote: str = "",
                       reason: str = "") -> str:
        lid = self._lead(lead)
        if not lid:
            raise ToolError("name the lead")
        if not resolved:
            self.audit.leads[lid] = "kept"
            return f"{lid} stays: {reason}".strip()
        try:
            text = self.bench._text(self.bench._path(file))
        except ToolError as e:
            raise ToolError(f"{file}: {e}") from None
        if not quoted(quote, text):
            raise ToolError(f"the quote is not verbatim in {file}")
        self.audit.leads[lid] = "resolved"
        self.audit.resolved[lid] = f"{file}: \"{quote.strip()}\""
        return f"{lid} resolved by {file}"

    def t_try_alternative(self, requirement_quote: str, test: str, assertion_quote: str,
                          why_valid: str, check_cmd: str, expected_failures: list,
                          lead: str = "") -> str:
        lead = self._lead(lead)
        task = self.bench.task
        if not quoted(requirement_quote, task.instruction or ""):
            raise ToolError("requirement_quote is not verbatim in the statement")
        blob = "\n".join((self.bench.tests or {}).values())
        if not quoted(assertion_quote, blob):
            raise ToolError("assertion_quote is not verbatim in the tests")
        if not names_a_test(test, blob):
            raise ToolError(f"{test!r} is not a test in the test files")
        self._spend()
        patch = self.bench.patch()
        g = self.bench.grade(patch)
        if g is None:
            return "your candidate doesn't apply to the workspace; show_changes"
        if g.passed:
            return "The verifier accepts this alternative, so it shows no ambiguity."
        failed: list[str] = []
        if g.report is not None and self.gold_passing:
            status = {k: v.name for k, v in g.report.status_by_name.items()}
            failed = [t for t in self.gold_passing if status.get(t) == "FAIL"]
            missing = [t for t in self.gold_passing if t not in status]
            if not failed:
                return ("The verifier rejected it, but no graded test failed (did it crash? "
                        f"exit {g.exit_code}). Fix it and try again.")
            want = [str(x) for x in expected_failures]
            stray = [t for t in failed if not any(_same_test(t, w) for w in want)]
            if stray or missing:
                return ("It is not otherwise complete: it also fails "
                        + ", ".join(stray + missing)
                        + ". Fix those so only the ambiguous point differs.")
        mine, ref = self._check_both(patch, check_cmd)
        if mine != 0:
            return "Your check of the stated requirement fails on your alternative itself."
        if ref != 0:
            return ("Your check fails on the reference fix, so it doesn't capture what the "
                    "statement asks; fix the check.")
        self.audit.alternatives.append(Alternative(
            requirement_quote, test, assertion_quote, why_valid, check_cmd, patch, failed,
            g.reward, lead))
        if lead:
            self.audit.leads[lead] = "confirmed"
        which = f" on {', '.join(failed)}" if failed else ""
        return (f"Confirmed: the verifier rejects your alternative{which}, which meets the "
                "stated requirement just as the reference does.")


def _same_test(report_id: str, named: str) -> bool:
    """``.graded.test_shop.test_logs`` ↔ ``test_logs`` / ``test_shop.py::test_logs``."""
    def last(x: str) -> str:
        return x.replace("::", ".").rstrip("()").split(".")[-1]
    return last(report_id) == last(named)
