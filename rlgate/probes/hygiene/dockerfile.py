"""§3.2 — static environment hygiene on the task's Dockerfile. Pure text inspection, no build.

These are determinism / isolation / leakage risks in the image recipe: a broad ``COPY .`` that
drags held-out material into the agent image, an unpinned base or dependency (non-reproducible
builds), and ``nproc`` (host-dependent parallelism). Each is a concrete line fact but its
exploitability depends on the surrounding setup, so REVIEW.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task

_BROAD_COPY = re.compile(r"^\s*(COPY|ADD)\s+(--\S+\s+)*\.(/)?(?=\s)")  # source is the whole context
_FROM = re.compile(r"^\s*FROM\s+(?P<img>[^\s]+)", re.IGNORECASE)
_NPROC = re.compile(r"\bnproc\b")
_PIP_RANGE = re.compile(r"\bpip\s+install\b.*(>=|<=|~=|!=|>(?!=)|<(?!=))")


def _dockerfile(task: Task) -> Path | None:
    img = task.grader.image
    if not img:
        return None
    p = Path(img)
    if p.is_dir():
        p = p / "Dockerfile"
    return p if p.is_file() else None


@register
class DockerfileHygiene(Probe):
    meta = ProbeMeta(
        id="hygiene.dockerfile",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        requires=frozenset({Capability.CONTAINER}),
        doc="Dockerfile hygiene: broad COPY, unpinned base/deps, nproc (determinism/leakage).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        path = _dockerfile(task)
        if path is None:
            return []
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            return []

        findings: list[Finding] = []
        for n, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            findings.extend(self._check_line(path.name, n, line))
        return findings

    def _check_line(self, fname: str, n: int, line: str):
        loc = f"{fname}:{n}"
        if _BROAD_COPY.match(line):
            yield self._f(loc, Severity.HIGH, "broad COPY/ADD pulls the whole context into the image",
                          "a broad copy can drag held-out tests / the reference solution into the "
                          "agent image", "copy only the needed paths; keep tests in a separate "
                          "verifier image (environment_mode=separate).")
        m = _FROM.match(line)
        if m and self._from_unpinned(m.group("img")):
            yield self._f(loc, Severity.MEDIUM, f"unpinned base image: {m.group('img')}",
                          "an untagged or :latest base makes the build non-reproducible",
                          "pin the base image by tag or @sha256 digest.")
        if _NPROC.search(line):
            yield self._f(loc, Severity.MEDIUM, "nproc makes the build host-dependent",
                          "nproc varies by host → non-deterministic build/test parallelism",
                          "hardcode the parallelism or derive it deterministically.")
        if _PIP_RANGE.search(line):
            yield self._f(loc, Severity.LOW, "unpinned pip dependency (version range)",
                          "a version range resolves differently over time → non-reproducible",
                          "pin exact versions (pkg==x.y.z) or use a locked requirements file.")

    def _from_unpinned(self, img: str) -> bool:
        if "@" in img:              # digest-pinned
            return False
        tag = img.rsplit(":", 1)[1] if ":" in img else ""
        return tag in ("", "latest")

    def _f(self, loc: str, sev: Severity, title: str, detail: str, fix: str) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            title=title,
            severity=sev,
            verdict=VerdictTier.REVIEW,
            location=loc,
            evidence=Evidence(kind="code_line", detail=detail, replay=f"sed -n '{loc.split(':')[1]}p' {loc.split(':')[0]}"),
            fix_hint=fix,
        )
