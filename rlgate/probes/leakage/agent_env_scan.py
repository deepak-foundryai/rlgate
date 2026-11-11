"""L0 — privileged information in the agent's environment (RG306).

A fresh agent environment (the task image, as the agent first sees it) is searched for files
whose names match the task's reference-solution and held-out test files; each match is then
compared by content hash. An identical copy is proof the agent can read the answer or the
tests straight off disk. Generic names (``test.sh``, ``__init__.py``, ``Dockerfile`` …) are
not searched for, so ordinary project files never match.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task

_GENERIC = {"test.sh", "run.sh", "run-tests.sh", "__init__.py", "conftest.py", "Dockerfile",
            ".dockerignore", "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
            "package.json", "README.md", "Makefile", "pytest.ini", "tox.ini", "go.mod",
            "go.sum", "docker-compose.yaml", "docker-compose.yml", ".gitignore"}
_SKIP_DIRS = ("/proc", "/sys", "/dev", "/run", "/var/lib/docker", "/usr/share")
_MAX_NAMES = 60


@register
class AgentEnvScan(Probe):
    meta = ProbeMeta(
        id="leakage.agent_env_scan",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.CRITICAL,
        needs_live=True,
        requires=frozenset({Capability.CONTAINER}),
        needs_baseline=False,      # inspects the environment only
        doc="The reference solution or held-out tests are on disk in the agent's environment.",
    )

    def applicable(self, task: Task) -> bool:
        return super().applicable(task) and bool(_secret_files(task))

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        secrets = _secret_files(task)
        public = _workspace_hashes(task.repo_dir)   # e.g. a scaffold copy of the workspace
        by_hash: dict[str, list[str]] = {}
        for label, f in secrets:
            h = _sha(f)
            if h not in public:
                by_hash.setdefault(h, []).append(label)
        secrets = [(lbl, f) for lbl, f in secrets if any(lbl in v for v in by_hash.values())]
        if not secrets:
            return []
        names = sorted({Path(lbl).name for lbl, _ in secrets})[:_MAX_NAMES]
        prune = " -o ".join(f"-path {d}" for d in _SKIP_DIRS) + \
            " -o -name node_modules -o -name .git"
        match = " -o ".join(f"-name {shlex.quote(n)}" for n in names)
        cmd = (f"find / -xdev \\( {prune} \\) -prune -o -type f \\( {match} \\) -print "
               "2>/dev/null | head -n 200 | while read -r f; do sha256sum \"$f\"; done")
        try:
            env = ctx.sandbox.agent_env()
        except NotImplementedError:
            return []
        with env:
            res = env.exec(cmd, timeout=600)
        out: list[Finding] = []
        for line in res.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2 or parts[0] not in by_hash:
                continue
            where, what = parts[1].strip(), by_hash[parts[0]][0]
            out.append(Finding(
                probe_id=self.meta.id,
                rule_id="RG306",
                title="The reference solution or tests are inside the agent's environment",
                severity=Severity.CRITICAL,
                verdict=VerdictTier.CONFIRMED,
                location=where,
                evidence=Evidence(
                    kind="reproduced_exploit",
                    detail=f"{where} in the agent's environment is identical to {what}",
                    replay=f"rlgate check {task.origin or task.id} --only leaked"),
            ))
        return out


def _secret_files(task: Task) -> list[tuple[str, Path]]:
    """(label, file) for the reference solution's files and the held-out test files."""
    dirs: list[tuple[Path, str]] = []
    if task.oracle is not None:
        dirs.append((Path(task.oracle[0]), "solution/"))
    dirs += [(p, pre) for p, pre in task.test_roots if p != task.repo_dir]
    out: list[tuple[str, Path]] = []
    for root, prefix in dirs:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_file() and f.name not in _GENERIC and f.stat().st_size >= 64 \
                    and "node_modules" not in f.parts:
                out.append((prefix + f.relative_to(root).as_posix(), f))
    return out


def _workspace_hashes(root: Path, budget: int = 20000) -> set[str]:
    """Hashes of the files the agent is meant to see: a held-out file identical to one of
    them is not a secret."""
    out: set[str] = set()
    n = 0
    for f in root.rglob("*"):
        if n >= budget:
            break
        if f.is_file() and not f.is_symlink() and "node_modules" not in f.parts \
                and ".git" not in f.parts and f.stat().st_size < 5_000_000:
            n += 1
            out.add(_sha(f))
    return out


def _sha(f: Path) -> str:
    return hashlib.sha256(f.read_bytes()).hexdigest()
