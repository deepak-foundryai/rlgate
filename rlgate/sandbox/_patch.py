"""Shared, deterministic patch application (zero fuzz: all-or-nothing). Used by every
sandbox backend so variant generation behaves identically regardless of where it runs."""

from __future__ import annotations

import subprocess
from pathlib import Path


class PatchConflict(RuntimeError):
    """A variant patch did not apply cleanly — bucketed as could-not-generate."""


def apply_zero_fuzz(root: Path, patch_text: str) -> None:
    """Apply a unified diff under `root` with no fuzz. Tries `git apply` then `patch`.

    Zero fuzz makes the result deterministic: a hunk applies at its exact location or the
    whole patch is rejected (raising PatchConflict)."""
    proc = subprocess.run(
        ["git", "apply", "--unsafe-paths", "-C0", "-p1", "-"],
        cwd=root, input=patch_text, text=True, capture_output=True, check=False,
    )
    if proc.returncode == 0:
        return
    proc = subprocess.run(
        ["patch", "-p1", "--no-backup-if-mismatch", "-F0", "-s"],
        cwd=root, input=patch_text, text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise PatchConflict(proc.stderr or proc.stdout)
