"""Waivers: a recorded decision to accept a finding instead of fixing it.

Stored in ``rlgate.toml`` NEXT TO THE TASK, one ``[[waivers]]`` table each::

    [[waivers]]
    rule = "RG403"
    reason = "the exact message is part of the spec"
    author = "Deepak Kumar"
    date = "2026-11-10"
    files = { "tests/test_api.py" = "9f2c…" }    # what the finding points at, hashed

Rules:
  - shown on every report as "accepted, with reason" — never hidden;
  - re-raised automatically when a file it refers to changes (the hash no longer matches),
    so a waiver can't silently cover a different problem later;
  - a Proven finding of high (or critical) severity can't be waived — only fixed.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from rlgate.core.finding import Finding, Severity, VerdictTier

FILE = "rlgate.toml"


class WaiverError(ValueError):
    pass


@dataclass
class Waiver:
    rule: str
    reason: str
    author: str = ""
    date: str = ""
    files: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"rule": self.rule, "reason": self.reason, "author": self.author,
                "date": self.date}


def task_root(origin: str | Path) -> Path:
    p = Path(origin)
    return p.parent if p.is_file() else p


def load(root: str | Path) -> list[Waiver]:
    path = task_root(root) / FILE
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise WaiverError(f"{path}: {e} — fix the file, or move it aside") from e
    out = []
    for w in data.get("waivers", []) or []:
        if isinstance(w, dict) and w.get("rule") and w.get("reason"):
            out.append(Waiver(str(w["rule"]).upper(), str(w["reason"]), str(w.get("author", "")),
                              str(w.get("date", "")), dict(w.get("files") or {})))
    return out


def referenced_files(f: Finding, root: Path) -> list[str]:
    """Task-relative files a finding points at (its location), when they exist here."""
    if not f.location:
        return []
    path = f.location.split(":", 1)[0].strip()
    for base in (root, root / "environment", root / "tests"):
        cand = base / path
        if cand.is_file():
            return [cand.relative_to(root).as_posix()]
    return []


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def fingerprint(f: Finding, root: Path) -> dict[str, str]:
    return {rel: _hash(root / rel) for rel in referenced_files(f, root)}


def is_stale(w: Waiver, root: Path) -> list[str]:
    """Files whose content changed (or vanished) since the waiver was recorded."""
    changed = []
    for rel, digest in w.files.items():
        p = root / rel
        if not p.is_file() or _hash(p) != digest:
            changed.append(rel)
    return changed


def apply(findings: list[Finding], root: str | Path) -> list[Waiver]:
    """Mark waived findings (``finding.waiver``). Returns waivers that are stale (their
    files changed — the finding is re-raised) so reports can say so."""
    root = task_root(root)
    stale: list[Waiver] = []
    for w in load(root):
        changed = is_stale(w, root)
        if changed:
            stale.append(w)
            continue
        for f in findings:
            if f.rule_id == w.rule and not _unwaivable(f):
                f.waiver = w.as_dict()
    return stale


def _unwaivable(f: Finding) -> bool:
    return f.verdict is VerdictTier.CONFIRMED and f.severity >= Severity.HIGH \
        and not _is_hygiene(f)


def _is_hygiene(f: Finding) -> bool:
    return f.rule_id.startswith("RG6")


def add(root: str | Path, f: Finding, reason: str, author: str | None = None) -> Waiver:
    """Record a waiver for finding ``f`` in ``<task>/rlgate.toml``."""
    root = task_root(root)
    if not reason.strip():
        raise WaiverError('a waiver needs a reason: --reason "why this is acceptable"')
    if _unwaivable(f):
        raise WaiverError(f"{f.rule_id} is a proven {f.severity.name.lower()}-severity "
                          "problem: it can only be fixed, not waived.")
    if any(w.rule == f.rule_id and not is_stale(w, root) for w in load(root)):
        raise WaiverError(f"{f.rule_id} is already waived for this task; rlgate waive "
                          "--list shows them")
    w = Waiver(f.rule_id, reason.strip(), author or _author(), time.strftime("%Y-%m-%d"),
               fingerprint(f, root))
    path = root / FILE
    block = ["", "[[waivers]]", f"rule = {json.dumps(w.rule)}",
             f"reason = {json.dumps(w.reason)}", f"author = {json.dumps(w.author)}",
             f"date = {json.dumps(w.date)}"]
    if w.files:
        inner = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in w.files.items())
        block.append(f"files = {{ {inner} }}")
    text = path.read_text() if path.is_file() else (
        "# rlgate settings for this task — see `rlgate init` for every option.\n")
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(block) + "\n")
    tomllib.loads(path.read_text())            # never leave a file we can't read back
    return w


def remove(root: str | Path, rule: str) -> int:
    """Delete every waiver of ``rule`` from ``<task>/rlgate.toml``; the number removed. Only
    the ``[[waivers]]`` tables of that rule go; the rest of the file is kept as written."""
    path = task_root(root) / FILE
    if not path.is_file():
        return 0
    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    block: list[str] = []
    removed = 0

    def flush() -> None:
        nonlocal removed
        if not block:
            return
        body = tomllib.loads("".join(block[1:]) or "")
        if str(body.get("rule", "")).upper() == rule.upper():
            removed += 1
            while out and not out[-1].strip():     # the blank line before it
                out.pop()
            out.append("\n")
        else:
            out.extend(block)
        block.clear()

    for line in lines:
        head = line.strip()
        if head.startswith("["):
            flush()
            if head == "[[waivers]]":
                block.append(line)
                continue
        (block if block else out).append(line)
    flush()
    text = "".join(out).rstrip("\n") + "\n"
    tomllib.loads(text)                       # never leave a file we can't read back
    path.write_text(text)
    return removed


def _author() -> str:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True,
                              check=False, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or getpass.getuser()
