"""L0 — test inputs and expected outputs the agent can change (RG213).

The grader's own scripts are read for absolute paths inside the agent's workspace. A path that
names test material (an expected output, a fixture, a test file or test directory) and that the
agent's edits would reach — anywhere in a shared environment, or under a declared artifact when
the verifier is separate — lets the agent edit the answer key instead of the code. Paths the
grader hash-checks or restores from its own copy first are not flagged. File-only.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task

_DATA_EXT = {".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".out", ".expected",
             ".golden", ".snap", ".npy", ".npz", ".pkl", ".parquet", ".dat", ".bin", ".ans"}
_TEST_WORDS = re.compile(r"(expected|fixture|golden|answer|snapshot|reference|testdata|"
                         r"(^|[/_.-])tests?([/_.-]|$)|_test\.|\.spec\.|\.test\.)", re.IGNORECASE)
_GUARDED = ("sha256sum", "sha1sum", "md5sum", "cmp ", "hashlib")


@register
class MutableTestData(Probe):
    meta = ProbeMeta(
        id="forgery.mutable_test_data",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        doc="The grader reads test files or expected outputs from where the agent can edit.",
    )

    def applicable(self, task: Task) -> bool:
        ws = task.workspace_env
        return ws is not None and bool(ws.workdir) and any(
            p != task.repo_dir for p, _ in task.test_roots)

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        assert task.workspace_env is not None and task.workspace_env.workdir
        workdir = PurePosixPath(task.workspace_env.workdir)
        ref = re.compile(re.escape(str(workdir)) + r"(/[A-Za-z0-9_./-]+)")
        separate = task.grader.env is not None
        reachable = [PurePosixPath(a) for a in task.grader.artifacts]
        out: list[Finding] = []
        seen: set[str] = set()
        for root, prefix in task.test_roots:
            if root == task.repo_dir:
                continue
            for f in sorted(root.rglob("*")):
                if not f.is_file() or f.name == "Dockerfile" or f.stat().st_size > 1_000_000:
                    continue
                try:
                    text = f.read_text(errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    for m in ref.finditer(line):
                        rel = m.group(1).strip("/").rstrip(".")
                        path = workdir / rel
                        if not rel or str(path) in seen or not _is_test_material(rel):
                            continue
                        if not (task.repo_dir / rel).exists():
                            continue                   # not shipped to the agent
                        if separate and not any(path == a or a in path.parents
                                                for a in reachable):
                            continue                   # agent edits never reach the grader
                        if _guarded(text, str(path)):
                            continue
                        seen.add(str(path))
                        out.append(Finding(
                            probe_id=self.meta.id,
                            rule_id="RG213",
                            title="The grader reads test data the agent can change",
                            severity=Severity.HIGH,
                            verdict=VerdictTier.REVIEW,
                            location=f"{prefix}{f.relative_to(root).as_posix()}:{n}",
                            evidence=Evidence(
                                kind="code_line",
                                detail=f"the grader reads {path}, which ships in the agent's "
                                       "workspace"
                                       + (" and is carried to the verifier as an artifact"
                                          if separate else "")
                                       + "; nothing checks it is unchanged",
                                replay=f"sed -n '{n}p' {f}"),
                        ))
        return out


def _is_test_material(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(_TEST_WORDS.search(rel)) or (p.suffix.lower() in _DATA_EXT and any(
        part.lower() in ("tests", "test", "data", "fixtures") for part in p.parts[:-1]))


def _guarded(text: str, path: str) -> bool:
    return any(g in line and path in line for line in text.splitlines() for g in _GUARDED)

