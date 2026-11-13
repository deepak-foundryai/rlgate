"""L0 — the grader installs or downloads software while grading (RG607).

Scans the grader's own scripts (the held-out test directory and the verifier's build context,
never the agent's workspace) for package installs and downloads executed at grading time.
These need open network while grading and can resolve to different versions run to run.
A Dockerfile is build time, so it is skipped. File-only.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task

_DOWNLOAD = re.compile(
    r"^(?!\s*#).*?\b(pip3?\s+install|uv\s+pip\s+install|uv\s+(tool\s+)?install|uvx\s|"
    r"npm\s+(install|i|ci)\b|npx\s+-y|yarn\s+(add|install)|pnpm\s+(add|install)|"
    r"apt(-get)?\s+install|apk\s+add|curl\s|wget\s|git\s+clone|go\s+install|"
    r"cargo\s+install|gem\s+install)")
_SCRIPTS = {".sh", ".bash", ""}
# talking to a service in the grader's own environment, or installing with no index: no
# download leaves the machine
_LOCAL = re.compile(r"(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|--no-index|--offline|"
                    r"--find-links|file://)")


@register
class GraderDownloads(Probe):
    meta = ProbeMeta(
        id="hygiene.grader_downloads",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.LOW,
        doc="The grader installs or downloads software while grading.",
    )

    def applicable(self, task: Task) -> bool:
        return bool(_grader_dirs(task))

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        out: list[Finding] = []
        for root, prefix in _grader_dirs(task):
            for f in sorted(root.rglob("*")):
                if not f.is_file() or f.suffix not in _SCRIPTS or f.name == "Dockerfile" \
                        or f.stat().st_size > 500_000:
                    continue
                try:
                    lines = f.read_text(errors="ignore").splitlines()
                except OSError:
                    continue
                if f.suffix == "" and not (lines and lines[0].startswith("#!")):
                    continue
                for n, line in enumerate(lines, 1):
                    m = _DOWNLOAD.search(line)
                    if not m or _LOCAL.search(line):
                        continue
                    why = _local_pip(line, task)
                    if why == "local":
                        continue
                    loc = f"{prefix}{f.relative_to(root).as_posix()}:{n}"
                    out.append(Finding(
                        probe_id=self.meta.id,
                        rule_id="RG607",
                        title="The grader downloads software while grading",
                        severity=Severity.LOW,
                        verdict=VerdictTier.REVIEW,
                        location=loc,
                        evidence=Evidence(kind="code_line",
                                          detail=f"runs `{m.group(1).strip()}` at grading "
                                                 f"time: {line.strip()[:120]}"
                                                 + (f" ({why})" if why else ""),
                                          replay=f"sed -n '{n}p' {f}"),
                    ))
                    break                       # one finding per script is enough
        return out


def _grader_dirs(task: Task) -> list[tuple[Path, str]]:
    """Test roots other than the agent's workspace: the grader's own files."""
    return [(p, pre) for p, pre in task.test_roots if p != task.repo_dir]


_PIP = re.compile(r"\b(?:python3?\s+-m\s+)?(?:uv\s+)?pip3?\s+install\s+(?P<args>[^;&|]*)")
_PIP_VALUE_OPTS = {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t",
                   "--target", "--prefix", "--root", "-i", "--index-url",
                   "--extra-index-url", "--config-settings", "-C", "--global-option",
                   "--install-option"}


def _local_pip(line: str, task: Task) -> str | None:
    """For ``pip install`` of the project itself (``.``, ``-e .``, a local path):
    "local" when nothing leaves the machine; a reason when it does (the project declares
    a build system and pip builds it in an isolated environment, fetching the build tools);
    None when the line installs anything else (a package name, a requirements file)."""
    m = _PIP.search(line)
    if not m:
        return None
    args = m.group("args").split()
    targets: list[str] = []
    isolated = True
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--no-build-isolation",):
            isolated = False
        elif a in ("-r", "--requirement", "-c", "--constraint"):
            return None                       # a requirements file: packages from an index
        elif a in ("-e", "--editable"):
            if i + 1 < len(args):
                targets.append(args[i + 1])
            i += 1
        elif a in _PIP_VALUE_OPTS:
            i += 1
        elif not a.startswith("-"):
            targets.append(a)
        i += 1
    local = [t for t in targets if t.strip("'\"") in (".", "./") or
             t.startswith(("./", "/", "../"))]
    if not targets or len(local) != len(targets):
        return None
    if not isolated:
        return "local"
    pyproject = task.repo_dir / "pyproject.toml"
    try:
        text = pyproject.read_text(errors="ignore") if pyproject.is_file() else ""
    except OSError:
        text = ""
    if "[build-system]" in text:
        return ("the project declares a [build-system], so pip builds it in an isolated "
                "environment and downloads the build tools")
    return "local"
