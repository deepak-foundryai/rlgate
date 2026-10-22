"""§3.9 / B-series — static over-fit proxy. A graded test that references a symbol introduced
only by the gold patch's internals and never named in the instruction is likely pinned to the
reference implementation (a private helper, an exact message/constant), so an alternative
correct solution would fail it.

This is a necessary-condition filter, not proof — it catches non-string internals (private
names, constants) that the equivalence probe's execution check doesn't reach — so REVIEW.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")  # length >= 5 cuts noise
# Only symbols the gold patch *defines* (functions / classes / types it introduces) count as
# implementation internals — not every token it touches (status_code, headers, … are ordinary
# vocabulary, not internals). A test calling a gold-defined helper is the real over-fit.
_DEF = re.compile(
    r"\b(?:def|class|func|function|interface|struct|type)\s+([A-Za-z_]\w{3,})"
    r"|\b(?:const|let|var)\s+([A-Za-z_]\w{3,})\s*=",
)
_TEST_GLOBS = ("test_*.py", "*_test.py", "*_test.go", "*.test.ts", "*.test.js",
               "*.spec.ts", "*.spec.js")
_PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
_STOP = {
    "assert", "assertion", "import", "return", "self", "true", "false", "none", "null",
    "value", "result", "expect", "equal", "equals", "should", "tests", "testcase", "setup",
    "teardown", "fixture", "mock", "patch", "class", "async", "await", "const", "print",
}
_MAX = 15


@register
class SymbolCrossref(Probe):
    meta = ProbeMeta(
        id="test_quality.symbol_crossref",
        family="test_quality",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        requires=frozenset({Capability.GOLD_PATCH, Capability.INSTRUCTION}),
        doc="A graded test references a gold-internal symbol the instruction never names.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        instr = {t.lower() for t in _IDENT.findall(task.instruction or "")}
        internals = self._gold_internals(task) - instr - _STOP
        if not internals:
            return []

        findings: list[Finding] = []
        for test_file in self._test_files(task.repo_dir):
            try:
                text = test_file.read_text(errors="ignore")
            except OSError:
                continue
            refs = {t.lower() for t in _IDENT.findall(text)} & internals
            for sym in sorted(refs):
                findings.append(self._finding(task, test_file, sym))
                if len(findings) >= _MAX:
                    return findings
        return findings

    def _gold_internals(self, task: Task) -> set[str]:
        added: list[str] = []
        for pf in task.gold_patch.patchset:
            for hunk in pf:
                added += [ln.value for ln in hunk if ln.is_added]
        names = set()
        for m in _DEF.finditer("\n".join(added)):
            names.add((m.group(1) or m.group(2)).lower())
        return names

    def _test_files(self, root: Path):
        seen: set[Path] = set()
        for glob in _TEST_GLOBS:
            for p in root.rglob(glob):
                if p in seen or any(part in _PRUNE for part in p.relative_to(root).parts):
                    continue
                if p.is_file():
                    seen.add(p)
                    yield p

    def _finding(self, task: Task, test_file: Path, sym: str) -> Finding:
        rel = test_file.relative_to(task.repo_dir).as_posix()
        return Finding(
            probe_id=self.meta.id,
            title=f"test references gold-internal '{sym}' not in the instruction: {rel}",
            severity=Severity.MEDIUM,
            verdict=VerdictTier.REVIEW,
            location=rel,
            evidence=Evidence(
                kind="code_line",
                detail=f"{rel} asserts on '{sym}', which the gold patch introduces but the "
                       "instruction never names; an alternative correct solution may not use it.",
                replay=f"grep -n {sym} {rel}",
            ),
            fix_hint="test the required behavior, not a reference-implementation internal; "
                     "or name the symbol in the instruction if it is part of the contract.",
        )
