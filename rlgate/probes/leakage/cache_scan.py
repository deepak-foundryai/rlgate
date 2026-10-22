"""§3.3 — the answer left on disk in caches / build artifacts / leftovers.

Three concrete, deterministic facts (no inference needed):
  - orphan bytecode: a ``__pycache__/*.pyc`` whose ``.py`` source is gone → the source was
    deleted but the compiled code remains, trivially decompilable. CONFIRMED.
  - patch leftovers: ``*.orig`` / ``*.rej`` hold pre/post-patch content verbatim. CONFIRMED.
  - answer-named files: ``solution* / answer* / expected* / *.gold / reference*`` → REVIEW.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task

_PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", ".tox"}
# Full-stem match only — bare "reference"/"expected"/"gold" over-match real code (a Reference
# class, a references table), so require a decisive answer-key word.
_ANSWER_RE = re.compile(
    r"^(solutions?|answers?|answer[_-]?key|ground[_-]?truth|expected[_-]?output|golden)$",
    re.IGNORECASE,
)
_ANSWER_EXT = {".gold", ".golden", ".expected"}
# Compiled / binary artifacts are build output, never a readable answer key.
_SKIP_EXT = {".class", ".pyc", ".pyo", ".o", ".so", ".a", ".jar", ".db", ".bin", ".png",
             ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz", ".wasm"}
_MAX = 25  # cap each category so a pathological tree can't flood the report


def _pruned(p: Path, root: Path) -> bool:
    return any(part in _PRUNE for part in p.relative_to(root).parts)


@register
class CacheScan(Probe):
    meta = ProbeMeta(
        id="leakage.cache_scan",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        doc="Answer recoverable from caches/artifacts (orphan .pyc, .orig/.rej, answer files).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        root = task.repo_dir
        findings: list[Finding] = []
        findings += self._orphan_bytecode(root)
        findings += self._leftovers(root)
        findings += self._answer_files(root)
        return findings

    def _orphan_bytecode(self, root: Path) -> list[Finding]:
        out: list[Finding] = []
        for pyc in root.rglob("__pycache__/*.pyc"):
            if _pruned(pyc, root):
                continue
            stem = pyc.name.split(".", 1)[0]               # mod.cpython-312.pyc -> mod
            if (pyc.parent.parent / f"{stem}.py").exists():
                continue                                    # source present → normal cache
            out.append(self._finding(
                root, pyc, VerdictTier.CONFIRMED, Severity.HIGH, "orphan bytecode",
                f"source {stem}.py is deleted but compiled bytecode remains (decompilable)",
                "ship the task without __pycache__; delete bytecode whose source was removed.",
            ))
            if len(out) >= _MAX:
                break
        return out

    def _leftovers(self, root: Path) -> list[Finding]:
        out: list[Finding] = []
        for pat in ("*.orig", "*.rej"):
            for f in root.rglob(pat):
                if _pruned(f, root) or not f.is_file():
                    continue
                out.append(self._finding(
                    root, f, VerdictTier.CONFIRMED, Severity.HIGH, "patch leftover",
                    "holds pre/post-patch content verbatim (the fix or its target)",
                    "remove *.orig / *.rej before shipping the task.",
                ))
                if len(out) >= _MAX:
                    return out
        return out

    def _answer_files(self, root: Path) -> list[Finding]:
        out: list[Finding] = []
        for f in root.rglob("*"):
            if _pruned(f, root) or f.is_symlink() or not f.is_file():
                continue
            if f.suffix.lower() in _SKIP_EXT:
                continue
            if _ANSWER_RE.match(f.stem) or f.suffix.lower() in _ANSWER_EXT:
                out.append(self._finding(
                    root, f, VerdictTier.REVIEW, Severity.MEDIUM, "answer-named file",
                    "filename suggests reference/expected output readable by the candidate",
                    "move reference material out of the candidate's tree, or rename if benign.",
                ))
                if len(out) >= _MAX:
                    break
        return out

    def _finding(self, root: Path, f: Path, verdict: VerdictTier, sev: Severity,
                 kind: str, why: str, fix: str) -> Finding:
        rel = f.relative_to(root).as_posix()
        return Finding(
            probe_id=self.meta.id,
            title=f"{kind}: {rel}",
            severity=sev,
            verdict=verdict,
            location=rel,
            evidence=Evidence(kind="code_line", detail=f"{rel} — {why}", replay=f"cat {rel}"),
            fix_hint=fix,
        )
