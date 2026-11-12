"""§3.3 — the MiMo mtime leak. A task is checked out so (nearly) all files share one
modification time; files injected afterwards (held-out tests, the reference solution, an
answer key) carry a distinctly later mtime. Any agent running ``ls -lt`` sees the outliers.

We detect a bimodal split: a majority "checkout" cluster and a clearly-later minority. The
minority is a leakage vector, not proven to be the answer, so REVIEW.
"""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task

_PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox",
          # written by the image build (pip install -e ., a compile step), not injected
          "build", "dist", ".eggs", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target",
          ".lake", ".gradle", "_build"}
_BUILT = (".egg-info", ".dist-info", ".pyc", ".pyo", ".so", ".o", ".class", ".pyd", ".dylib",
          ".dll", ".a", ".mo")
# compiled from a sibling source by the build: Cython's x.pyx → x.c / x.cpp
_FROM = {".c": (".pyx", ".py"), ".cpp": (".pyx", ".py"), ".h": (".pyx", ".pxd")}
# written by setuptools-scm / versioneer when the package is installed
_VERSION_FILES = {"_version.py", "version.py", "__version__.py", "_version.txt"}
_MIN_FILES = 8          # below this there isn't enough signal to call a cluster
_GAP_SECONDS = 120      # a cluster this much later than the bulk is suspicious
_MAX_MINORITY = 0.5     # the late cluster must be a minority to look "injected"
_SAMPLE = 8


def _mtimes(root: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).parts
        if any(part in _PRUNE or part.endswith(_BUILT) for part in rel):
            continue
        if p.is_symlink() or not p.is_file():
            continue
        if p.name in _VERSION_FILES or any(
                p.with_suffix(src).is_file() for src in _FROM.get(p.suffix, ())) or any(
                p.with_name(p.name + t).is_file() for t in (".tp", ".in", ".tmpl")):
            continue
        try:
            out.append((p.relative_to(root).as_posix(), int(p.stat().st_mtime)))
        except OSError:
            continue
    return out


@register
class MtimeClustering(Probe):
    meta = ProbeMeta(
        id="leakage.mtime_clustering",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        needs_baseline=False,      # inspects the environment only
        doc="A minority of files share a distinctly later mtime (injected after checkout).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        files = _mtimes(task.repo_dir)
        if len(files) < _MIN_FILES:
            return []

        times = sorted({t for _, t in files})
        if len(times) < 2:
            return []
        # Largest adjacent gap in the sorted distinct mtimes.
        gaps = [(times[i + 1] - times[i], times[i]) for i in range(len(times) - 1)]
        gap, boundary = max(gaps)
        if gap < _GAP_SECONDS:
            return []

        late = [(p, t) for p, t in files if t > boundary]
        if not late or len(late) > _MAX_MINORITY * len(files):
            return []  # not a minority → not the injected-answer shape

        sample = sorted(p for p, _ in late)[:_SAMPLE]
        more = "" if len(late) <= _SAMPLE else f" (+{len(late) - _SAMPLE} more)"
        return [Finding(
            probe_id=self.meta.id,
            title=f"{len(late)} file(s) written {gap}s after the repo checkout",
            severity=Severity.MEDIUM,
            verdict=VerdictTier.REVIEW,
            location=sample[0],
            evidence=Evidence(
                kind="code_line",
                detail=f"these files' mtime is a clear outlier from the {len(files) - len(late)}-"
                       f"file bulk; visible via `ls -lt`: {', '.join(sample)}{more}",
                replay="ls -lt -R | head",
            ),
            fix_hint="normalize mtimes at build (touch -d to a fixed epoch), or inject held-out "
                     "material in the verifier container the candidate never sees.",
        )]
