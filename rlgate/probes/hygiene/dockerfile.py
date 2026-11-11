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
from rlgate.core.task import Task

_BROAD_COPY = re.compile(r"^\s*(COPY|ADD)\s+(--\S+\s+)*\.(/)?(?=\s)")  # source is the whole context
_FROM = re.compile(r"^\s*FROM\s+(?P<img>[^\s]+)", re.IGNORECASE)
_NPROC = re.compile(r"\bnproc\b")
_PIP_RANGE = re.compile(r"\bpip\s+install\b.*(>=|<=|~=|!=|>(?!=)|<(?!=))")


def _dockerfiles(task: Task) -> list[tuple[Path, str, bool]]:
    """Every Dockerfile the task builds (agent image, verifier image, legacy grader image),
    with the name shown in findings (relative to the task folder when known) and whether it
    is the verifier's image (where copying the tests in is the point)."""
    cands: list[Path] = []
    for env in (task.workspace_env, task.grader.env):
        if env is not None and env.dockerfile is not None:
            cands.append(env.dockerfile)
    verifier = {cands[-1].resolve()} if (task.grader.env is not None and cands
                                          and task.grader.env.dockerfile is not None
                                          and task.grader.env != task.workspace_env) else set()
    img = task.grader.image
    if img:
        p = Path(img)
        cands.append(p / "Dockerfile" if p.is_dir() else p)
    out: list[tuple[Path, str, bool]] = []
    seen: set[Path] = set()
    base = Path(task.origin) if task.origin else None
    for p in cands:
        if not p.is_file() or p.resolve() in seen:
            continue
        seen.add(p.resolve())
        try:
            name = p.resolve().relative_to(base.resolve()).as_posix() if base else p.name
        except ValueError:
            name = p.name
        out.append((p, name, p.resolve() in verifier))
    return out


# dependency installs, and the manifests they read (copying only these keeps the layer cached)
_INSTALL = re.compile(r"\b(pip3?\s+install|uv\s+(pip\s+install|sync)|poetry\s+install|"
                      r"npm\s+(ci|install|i)\b|yarn(\s+install)?\s*($|&&)|pnpm\s+install|"
                      r"go\s+mod\s+download|cargo\s+(fetch|build)|bundle\s+install|"
                      r"composer\s+install|mvn\s+.*dependency|gradle\s+.*dependencies|"
                      r"apt(-get)?\s+install|apk\s+add)")
_MANIFEST = re.compile(r"(requirements[^/\s]*\.(txt|in)|constraints[^/\s]*\.txt|"
                       r"pyproject\.toml|setup\.(py|cfg)|poetry\.lock|uv\.lock|Pipfile(\.lock)?|"
                       r"package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|\.npmrc|go\.(mod|sum)|"
                       r"Cargo\.(toml|lock)|Gemfile(\.lock)?|composer\.(json|lock)|pom\.xml|"
                       r"build\.gradle(\.kts)?|[^/\s]*\.csproj|[^/\s]*\.opam(\.locked)?|"
                       r"[^/\s]*\.sh)$")
_COPY = re.compile(r"^\s*(COPY|ADD)\s+(?P<args>.+)$", re.IGNORECASE)


def _copies_source(line: str) -> bool:
    """A COPY/ADD of anything beyond dependency manifests (and not from another stage)."""
    m = _COPY.match(line)
    if not m:
        return False
    parts = [a for a in m.group("args").split() if not a.startswith("--")]
    if "--from" in line.lower():
        return False
    if m.group("args").strip().startswith("["):
        return True
    sources = parts[:-1]
    return any(not _MANIFEST.search(src.rstrip("/")) for src in sources)


@register
class DockerfileHygiene(Probe):
    # Dockerfiles are read wherever the task keeps them; no container is needed to read one
    meta = ProbeMeta(
        id="hygiene.dockerfile",
        family="hygiene",
        kind=ProbeKind.GENERIC,
        severity=Severity.MEDIUM,
        doc="Dockerfile hygiene: broad COPY, unpinned base/deps, nproc (determinism/leakage).",
    )

    def applicable(self, task: Task) -> bool:
        return bool(_dockerfiles(task))

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        findings: list[Finding] = []
        for path, name, is_verifier in _dockerfiles(task):
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            lines = text.splitlines()
            for n, raw in enumerate(lines, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                findings.extend(f for f in self._check_line(name, n, line)
                                if not (is_verifier and f.rule_id == "RG601"))
            findings.extend(self._layer_order(name, lines))
        return findings

    def _layer_order(self, fname: str, lines: list[str]):
        """RG606: a dependency install after a COPY of source, in the same stage."""
        copied_at: int | None = None
        for n, raw in enumerate(_joined(lines), start=1):
            line = raw.strip()
            if re.match(r"^\s*FROM\s", line, re.IGNORECASE):
                copied_at = None
                continue
            if copied_at is None and _copies_source(line):
                copied_at = n
                continue
            if copied_at is not None and re.match(r"^\s*RUN\s", line, re.IGNORECASE) \
                    and _INSTALL.search(line):
                yield self._f("RG606", f"{fname}:{n}", Severity.LOW,
                              "Dependencies install after the source is copied",
                              f"line {n} installs dependencies after line {copied_at} copies "
                              "the task's source, so any source change re-runs the install",
                              "copy the dependency manifests and install them before copying "
                              "the rest of the source.")
                return

    def _check_line(self, fname: str, n: int, line: str):
        loc = f"{fname}:{n}"
        if _BROAD_COPY.match(line):
            yield self._f("RG601", loc, Severity.HIGH, "broad COPY/ADD pulls the whole context into the image",
                          "a broad copy can drag held-out tests / the reference solution into the "
                          "agent image", "copy only the needed paths; keep tests in a separate "
                          "verifier image (environment_mode=separate).")
        m = _FROM.match(line)
        if m and self._from_unpinned(m.group("img")):
            yield self._f("RG602", loc, Severity.MEDIUM, f"unpinned base image: {m.group('img')}",
                          "an untagged or :latest base makes the build non-reproducible",
                          "pin the base image by tag or @sha256 digest.")
        if _NPROC.search(line):
            yield self._f("RG603", loc, Severity.MEDIUM, "nproc makes the build host-dependent",
                          "nproc varies by host → non-deterministic build/test parallelism",
                          "hardcode the parallelism or derive it deterministically.")
        if _PIP_RANGE.search(line):
            yield self._f("RG604", loc, Severity.LOW, "unpinned pip dependency (version range)",
                          "a version range resolves differently over time → non-reproducible",
                          "pin exact versions (pkg==x.y.z) or use a locked requirements file.")

    @staticmethod
    def _from_unpinned(img: str) -> bool:
        if "@" in img:              # digest-pinned
            return False
        tag = img.rsplit(":", 1)[1] if ":" in img else ""
        return tag in ("", "latest")

    def _f(self, rule_id: str, loc: str, sev: Severity, title: str, detail: str,
           fix: str) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            rule_id=rule_id,
            title=title,
            severity=sev,
            verdict=VerdictTier.REVIEW,
            location=loc,
            evidence=Evidence(kind="code_line", detail=detail, replay=f"sed -n '{loc.split(':')[1]}p' {loc.split(':')[0]}"),
            fix_hint=fix,
        )


def _joined(lines: list[str]) -> list[str]:
    """Continuation lines folded into their instruction, keeping line numbers aligned (the
    folded text sits on the instruction's first line; the rest become blank)."""
    out: list[str] = []
    buf: list[str] = []
    for raw in lines:
        if buf or raw.rstrip().endswith("\\"):
            buf.append(raw.rstrip().rstrip("\\"))
            if raw.rstrip().endswith("\\"):
                continue
            out.append(" ".join(buf))
            out.extend([""] * (len(buf) - 1))
            buf = []
        else:
            out.append(raw)
    if buf:
        out.append(" ".join(buf))
        out.extend([""] * (len(buf) - 1))
    return out
