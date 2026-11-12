"""§3.9 / B-series — static over-fit proxy. A graded test that references a symbol introduced
only by the gold patch's internals and never named in the instruction is likely pinned to the
reference implementation (a private helper, an exact message/constant), so an alternative
correct solution would fail it.

This is a necessary-condition filter, not proof — it catches non-string internals (private
names, constants) that the equivalence probe's execution check doesn't reach — so REVIEW.
"""

from __future__ import annotations

import builtins
import re

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.probes.test_quality._graded import graded_tests

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")  # length >= 5 cuts noise
# Only symbols the gold patch *defines* (functions / classes / types it introduces) count as
# implementation internals — not every token it touches (status_code, headers, … are ordinary
# vocabulary, not internals). A test calling a gold-defined helper is the real over-fit.
_DEF = re.compile(
    r"\b(?:def|class|func|function|interface|struct|type)\s+([A-Za-z_]\w{3,})"
    r"|\b(?:const|let|var)\s+([A-Za-z_]\w{3,})\s*=",
)
_STOP = {
    "assert", "assertion", "import", "return", "self", "true", "false", "none", "null",
    "value", "result", "expect", "equal", "equals", "should", "tests", "testcase", "setup",
    "teardown", "fixture", "mock", "patch", "class", "async", "await", "const", "print",
}
_MAX = 15
# Python and JS/TS builtins: a test calling sorted() doesn't use the reference's sorted
_BUILTINS = {n.lower() for n in dir(builtins)} | {
    "array", "object", "string", "number", "boolean", "promise", "symbol", "error",
    "sorted", "filter", "reduce", "keys", "values", "entries", "parse", "stringify"}


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
        instr = _named(task.instruction or "")
        internals = self._gold_internals(task) - instr - _STOP
        # a method the fix overrides (abatch, get_graph on a new subclass) is the code
        # base's API, already there before the change: not the reference's internals
        internals -= _preexisting(task.repo_dir, internals)
        if not internals:
            return []

        findings: list[Finding] = []
        for rel, text in graded_tests(task):
            # what the test defines itself (a helper of its own) isn't the reference's
            own = {(m.group(1) or m.group(2)).lower() for m in _DEF.finditer(text)}
            refs = ({t.lower() for t in _IDENT.findall(text)} & internals) - own
            for sym in sorted(refs):
                findings.append(self._finding(rel, sym))
                if len(findings) >= _MAX:
                    return findings
        return findings

    def _gold_internals(self, task: Task) -> set[str]:
        added: list[str] = []
        if task.gold_patch is None:
            return set()
        for pf in task.gold_patch.patchset:
            for hunk in pf:
                added += [ln.value for ln in hunk if ln.is_added]
        names = set()
        for m in _DEF.finditer("\n".join(added)):
            name = m.group(1) or m.group(2)
            if name.startswith("__") and name.endswith("__"):
                continue            # a dunder (__init__, __hash__) is the language's, not an internal
            names.add(name.lower())
        return names - _BUILTINS

    def _finding(self, rel: str, sym: str) -> Finding:
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


def _named(text: str) -> set[str]:
    """Identifiers the instruction names, also in prose: "batch-as-completed" and "batch as
    completed" both name batch_as_completed."""
    names = {t.lower() for t in _IDENT.findall(text)}
    words = re.findall(r"\w+", text.replace("-", "_").lower())
    names |= {w for w in words if len(w) >= 5}
    for n in (2, 3, 4):
        names |= {"_".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    return names


_CODE = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt",
         ".scala", ".rb", ".php", ".cs", ".swift", ".dart", ".c", ".cc", ".cpp", ".h", ".hpp"}
_PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
_MAX_FILES = 20_000
_MAX_BYTES = 1_000_000


def _preexisting(root, names: set[str]) -> set[str]:
    """The names already defined in the pre-change repository."""
    if not names or root is None or not root.is_dir():
        return set()
    pat = re.compile(r"\b(?:def|class|func|function|fn|interface|struct|type)\s+("
                     + "|".join(sorted(map(re.escape, names))) + r")\b", re.IGNORECASE)
    found: set[str] = set()
    seen = 0
    for p in root.rglob("*"):
        if p.suffix not in _CODE or any(part in _PRUNE for part in p.relative_to(root).parts):
            continue
        seen += 1
        if seen > _MAX_FILES:
            break
        try:
            if not p.is_file() or p.stat().st_size > _MAX_BYTES:
                continue
            found |= {m.group(1).lower() for m in pat.finditer(p.read_text(errors="ignore"))}
        except OSError:
            continue
        if found >= names:
            break
    return found
