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


def _dockerfiles(task: Task) -> list[tuple[Path, str, bool, bool]]:
    """Every Dockerfile the task builds (agent image, verifier image, legacy grader image),
    with the name shown in findings (relative to the task folder when known), whether it
    is the verifier's image (where copying the tests in is the point), and whether it is
    only the recipe of a prebuilt image the task runs instead."""
    cands: list[Path] = []
    for env in (task.workspace_env, task.grader.env):
        if env is not None and env.dockerfile is not None:
            cands.append(env.dockerfile)
        elif env is not None and env.compose is not None:   # the stack's own Dockerfile
            cands.append(env.compose.parent / "Dockerfile")
    verifier = {cands[-1].resolve()} if (task.grader.env is not None and cands
                                          and task.grader.env.dockerfile is not None
                                          and task.grader.env != task.workspace_env) else set()
    img = task.grader.image
    # a task that runs a prebuilt image (docker_image) never builds the Dockerfile beside
    # it: that file is only the recipe the image came from, so only what it copied in
    # (RG601) still says something about the image
    recipe: set[Path] = set()
    if img:
        p = Path(img)
        p = p / "Dockerfile" if p.is_dir() else p
        built = {c.resolve() for c in cands}
        if (task.workspace_env is not None or task.grader.env is not None) \
                and p.resolve() not in built:
            recipe.add(p.resolve())
        cands.append(p)
    out: list[tuple[Path, str, bool, bool]] = []
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
        out.append((p, name, p.resolve() in verifier, p.resolve() in recipe))
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
        for path, name, is_verifier, recipe in _dockerfiles(task):
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            lines = text.splitlines()
            for n, raw in enumerate(_instructions(lines), start=1):   # one per line
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                for f in self._check_line(name, n, line):
                    if recipe and f.rule_id != "RG601":
                        continue
                    if f.rule_id == "RG601":
                        # a broad copy matters only when the context holds held-out material
                        held = None if is_verifier else _held_out_in(task, path.parent)
                        if held is None:
                            continue
                        f.evidence.detail = (f"line {n} copies the whole build context, "
                                             f"which includes {held}")
                    findings.append(f)
            if not recipe:
                findings.extend(self._layer_order(name, lines))
        return findings

    def _layer_order(self, fname: str, lines: list[str]):
        """RG606: a dependency install after a COPY of source, in the same stage."""
        copied_at: int | None = None
        for n, raw in enumerate(_instructions(lines), start=1):
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


_SCAN_LIMIT = 20000                   # files looked at per build context
_MIN_SIZE = 64                        # smaller files (empty __init__.py, …) prove nothing


def _held_out_dirs(task: Task) -> list[Path]:
    """Where the task keeps what the agent must not see: the reference solution, the tests
    uploaded at grading, and a separate verifier image's build context."""
    dirs = [Path(host) for host, _ in task.grader.inject]
    if task.oracle:
        dirs.append(Path(task.oracle[0]))
    g = task.grader.env
    if g is not None and g != task.workspace_env and g.context is not None:
        dirs.append(g.context)
    return [d.resolve() for d in dirs if d.is_dir()]


def _held_out_in(task: Task, context: Path) -> str | None:
    """The held-out file a build context contains (the folder itself, or a byte-identical
    copy of one of its files), named relative to the context; None when there is none."""
    import hashlib
    import os
    ctx = context.resolve()
    held = _held_out_dirs(task)
    for d in held:
        if d == ctx or d.is_relative_to(ctx):
            return f"{d.relative_to(ctx).as_posix() or '.'}/ (held out)"
        if ctx.is_relative_to(d):
            return None                    # the context is itself held-out material
    sigs: dict[int, set[str]] = {}
    paths: dict[str, str] = {}
    for d in held:
        for root, _, files in os.walk(d):
            for fn in files:
                fp = Path(root, fn)
                try:
                    size = fp.stat().st_size
                    if size < _MIN_SIZE:
                        continue
                    digest = hashlib.sha256(fp.read_bytes()).hexdigest()
                except OSError:
                    continue
                sigs.setdefault(size, set()).add(digest)
                paths.setdefault(digest, fp.relative_to(d.parent).as_posix())
    if not sigs:
        return None
    seen = 0
    for root, dirs, files in os.walk(ctx):
        dirs[:] = [x for x in dirs if x != ".git"]
        for fn in files:
            seen += 1
            if seen > _SCAN_LIMIT:
                return None
            fp = Path(root, fn)
            try:
                size = fp.stat().st_size
                if size not in sigs:
                    continue
                digest = hashlib.sha256(fp.read_bytes()).hexdigest()
            except OSError:
                continue
            if digest in sigs[size]:
                return (f"{fp.relative_to(ctx).as_posix()}, a copy of the held-out "
                        f"{paths[digest]}")
    return None


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def _instructions(lines: list[str]) -> list[str]:
    """One Dockerfile instruction per line, numbers kept: heredoc bodies (``RUN <<EOF``) and
    comment lines blanked, continuation lines folded into their instruction."""
    out: list[str] = []
    end: str | None = None
    cont = False                      # inside a continued instruction
    for raw in lines:
        if end is not None:
            out.append("")
            if raw.strip() == end:
                end = None
            continue
        if raw.lstrip().startswith("#"):
            out.append("\\" if cont else "")    # a comment doesn't end a continuation
            continue
        out.append(raw)
        cont = raw.rstrip().endswith("\\")
        m = _HEREDOC.search(raw)
        if m:
            end, cont = m.group(1), False
    return _joined(out)


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
