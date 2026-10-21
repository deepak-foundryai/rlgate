"""Deterministic variant GENERATION (see ARCHITECTURE §2a).

The gold patch has an explicit, ordered hunk structure. A revert variant = the gold patch
with exactly one hunk removed — a minimal, known-wrong solution (non-equivalent by
construction). Generation is a pure function of the patch bytes: no RNG, no search.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from rlgate.core.task import Patch

OPERATORS_VERSION = "operators@v1"


@dataclass(frozen=True)
class Variant:
    id: str                 # content-addressed; identical inputs → identical id
    operator: str           # "revert" | "stub" | …
    patch_text: str         # the (gold-minus-hunk) patch to apply on the base tree
    origin: str             # human-readable location, e.g. "path/to/file.py#hunk2"


def _variant_id(gold_sha: str, origin: str, operator: str) -> str:
    key = f"{gold_sha}|{origin}|{operator}|{OPERATORS_VERSION}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def revert_variants(gold: Patch) -> list[Variant]:
    """One variant per hunk: the gold patch with that single hunk omitted.

    Hunks are addressed by (file, hunk_index) from the parsed diff — stable and ordered.
    """
    ps = gold.patchset
    variants: list[Variant] = []
    for pf in ps:
        for i, _hunk in enumerate(pf):
            origin = f"{pf.path}#hunk{i}"
            text = _patch_without(ps, pf.path, i)
            if text is None:
                continue  # omitting this hunk left the file empty of changes; skip
            variants.append(Variant(
                id=_variant_id(gold.sha256, origin, "revert"),
                operator="revert",
                patch_text=text,
                origin=origin,
            ))
    return variants


def _patch_without(ps, file_path: str, drop_index: int) -> str | None:
    """Rebuild the unified diff omitting one hunk of one file. Pure string transform."""
    out_files = []
    for pf in ps:
        kept = [h for j, h in enumerate(pf) if not (pf.path == file_path and j == drop_index)]
        if not kept:
            continue
        header = _file_header(pf)
        out_files.append(header + "".join(str(h) for h in kept))
    text = "".join(out_files)
    return text or None


def _file_header(pf) -> str:
    # unidiff exposes the per-file header lines; reconstruct the minimal valid header.
    src = pf.source_file or ("a/" + pf.path)
    tgt = pf.target_file or ("b/" + pf.path)
    return f"--- {src}\n+++ {tgt}\n"
