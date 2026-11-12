"""§3.9 — brittle test assertions that fingerprint the reference implementation (static).

Two generic detectors over the graded test files:
  - exact-string pinning: a test asserts a string literal that also appears verbatim in the
    gold source (and the instruction doesn't name it) → the test is pinned to the reference's
    wording, not the behavior;
  - call-count assertions: a test pins exact invocation counts (assert_called_once,
    .call_count ==, toHaveBeenCalledTimes, …) → an impl fingerprint; a different-but-correct
    solution with a different call shape fails.

Both are necessary-condition proxies (an alternative solution *may* fail), so REVIEW.
"""

from __future__ import annotations

import re

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.probes.test_quality._graded import graded_tests

# whole literals, left to right: a pattern without quotes inside ran from one string's
# closing quote to the next one's opening quote ("'] == '")
_STRING = re.compile(r"""(['"])((?:\\.|(?!\1)[^\\\n])*)\1""")
_CALL_COUNT = re.compile(
    r"assert_called_once|\.call_count\s*==|toHaveBeenCalledTimes|\.mock\.calls\.length|"
    r"\.Times\(\s*\d",
)
_MAX = 20


@register
class BrittleAssertions(Probe):
    meta = ProbeMeta(
        id="test_quality.brittle_assertions",
        family="test_quality",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        doc="Graded tests pin exact strings from gold source or exact call counts (fingerprint).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        gold_strings = self._gold_strings(task)
        instr = (task.instruction or "").lower()
        findings: list[Finding] = []
        for rel, text in graded_tests(task):
            findings += self._pinned_strings(rel, text, gold_strings, instr)
            findings += self._call_counts(rel, text)
            if len(findings) >= _MAX:
                return findings[:_MAX]
        return findings

    def _gold_strings(self, task: Task) -> set[str]:
        if task.gold_patch is None:
            return set()
        added: list[str] = []
        for pf in task.gold_patch.patchset:
            for hunk in pf:
                added += [ln.value for ln in hunk if ln.is_added]
        return {m.group(2) for m in _STRING.finditer("\n".join(added)) if len(m.group(2)) >= 4}

    def _pinned_strings(self, rel, text, gold_strings, instr):
        if not gold_strings:
            return []
        hits = {m.group(2) for m in _STRING.finditer(text) if len(m.group(2)) >= 4} \
            & gold_strings
        out = []
        for s in sorted(hits):
            if " " not in s.strip():
                continue  # an identifier/dict-key (account_id) is normal; wording is a phrase
            if s.lower() in instr:
                continue  # the instruction names it → a real requirement, not a fingerprint
            out.append(self._f(
                rel, "exact-string pinning",
                f"asserts the literal {s!r}, which also appears verbatim in the gold source; "
                "a correct solution that words it differently would fail.",
                "assert the behavior, not the reference's exact wording.",
            ))
        return out

    def _call_counts(self, rel, text):
        if not _CALL_COUNT.search(text):
            return []
        return [self._f(
            rel, "call-count assertion",
            "pins an exact invocation count / call shape (assert_called_once, .call_count, "
            "toHaveBeenCalledTimes, …); a correct solution with a different call shape fails.",
            "assert observable behavior/outputs, not the number of internal calls.",
        )]

    def _f(self, rel: str, kind: str, detail: str, fix: str) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            rule_id="RG403" if kind == "exact-string pinning" else "RG404",
            title=f"{kind}: {rel}",
            severity=Severity.MEDIUM,
            verdict=VerdictTier.REVIEW,
            location=rel,
            evidence=Evidence(kind="code_line", detail=detail, replay=f"cat {rel}"),
            fix_hint=fix,
        )
