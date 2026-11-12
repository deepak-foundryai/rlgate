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
_OWN_COUNTER = re.compile(r"\bself\.call_count\s*\+?=")
_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%[-+ 0#]*\d*(?:\.\d+)?[sdifr]")
_WORD = re.compile(r"[A-Za-z]{2,}")


def _named(s: str, instr: str) -> bool:
    """The instruction spells the literal out: verbatim, or with its f-string / %
    placeholders filled in (``'{n} items found'`` against "prints `3 items found`")."""
    s = s.lower()
    if s in instr:
        return True
    parts = [re.escape(x) for x in _PLACEHOLDER.split(s)]
    if len(parts) < 2:
        return False
    return re.search(r".{1,40}?".join(parts), instr) is not None


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
            fixed = _PLACEHOLDER.sub("\0", s)
            if " " not in s.strip() or not any(
                    len(_WORD.findall(seg)) >= 2 for seg in fixed.split("\0")):
                continue  # an identifier, a key, a format ('x{i}', ' + {term}'): not wording
            if _named(s, instr):
                continue  # the instruction names it → a real requirement, not a fingerprint
            out.append(self._f(
                rel, "exact-string pinning",
                f"asserts the literal {s!r}, which also appears verbatim in the gold source; "
                "a correct solution that words it differently would fail.",
                "assert the behavior, not the reference's exact wording.",
            ))
        return out

    def _call_counts(self, rel, text):
        hits = {m.group(0) for m in _CALL_COUNT.finditer(text)}
        if not hits:
            return []
        if hits <= {m for m in hits if m.startswith(".call_count")} and \
                _OWN_COUNTER.search(text):
            return []   # the test's own fake counts its calls: "the backend ran once" is
            #             the behaviour under test (coalescing, caching), not a call shape
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
