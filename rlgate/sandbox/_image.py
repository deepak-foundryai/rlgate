"""Shared image resolution for container backends.

``grader.image`` is either a local Dockerfile (or a directory holding one — Harbor tasks
ship ``environment/Dockerfile``) or a registry reference. Every container backend must
interpret it the same way, or the same task grades differently depending on where it runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


def resolve_dockerfile(image: str | None) -> Path | None:
    """The local Dockerfile ``image`` points at, or None if it's a registry ref/unset."""
    if not image:
        return None
    p = Path(image)
    if p.is_file():
        return p
    if p.is_dir() and (p / "Dockerfile").is_file():
        return p / "Dockerfile"
    return None


def context_hash(dockerfile: Path, extra: str = "") -> str:
    """Content hash of a Dockerfile plus its whole build context (the Dockerfile's dir).

    Any byte change in the recipe or a file it could COPY gives a new hash, so a cached
    image/snapshot keyed on it can never be stale. ``extra`` folds in build parameters
    (e.g. resources) that also distinguish one build from another."""
    h = hashlib.sha256()
    root = dockerfile.parent
    h.update(dockerfile.name.encode())
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or not f.is_file():
            continue
        h.update(rel.as_posix().encode() + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    h.update(extra.encode())
    return h.hexdigest()
