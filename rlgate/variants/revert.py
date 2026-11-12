"""Deterministic variant GENERATION (see ARCHITECTURE §2a).

The gold patch has an explicit, ordered hunk structure. A revert variant = the gold patch
with exactly one hunk removed — a minimal, known-wrong solution (non-equivalent by
construction). Generation is a pure function of the patch bytes: no RNG, no search.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rlgate.core.task import Patch
from rlgate.variants._code import HASH, SLASH, mask

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


def revert_variants(gold: Patch, root: Path | None = None) -> list[Variant]:
    """One variant per hunk: the gold patch with that single hunk omitted.

    Hunks are addressed by (file, hunk_index) from the parsed diff — stable and ordered.
    Skipped, since leaving them out can't be "a part of the fix": build output and caches
    (a patch derived from a solution script picks up what it compiled), and hunks that only
    touch blank lines, comments or (Python, given the pre-change tree ``root``) docstrings
    and formatting.
    """
    ps = gold.patchset
    variants: list[Variant] = []
    for pf in ps:
        if _vcs_metadata(pf.path) or generated(pf.path):
            continue    # git's own files, build output: no test guards a hunk there
        for i, hunk in enumerate(pf):
            if _inert(pf, i, hunk, root):
                continue
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


_GEN_DIRS = {".lake", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "node_modules", ".gradle", ".tox", "CMakeFiles", ".cache", ".hypothesis"}
# (not .d or .v: D and Coq/Verilog source share them)
_GEN_SUFFIXES = {".pyc", ".pyo", ".o", ".obj", ".a", ".class", ".aux", ".glob", ".vo",
                 ".vok", ".vos", ".olean", ".ilean", ".trace", ".hash", ".log"}
_COMMENT = {
    "#": (".py", ".sh", ".bash", ".rb", ".pl", ".r", ".yaml", ".yml", ".toml", ".cfg",
          ".ini", ".mk", ".cmake", ".jl", ".nim", ".ex", ".exs"),
    "//": (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".js", ".mjs", ".cjs", ".ts", ".tsx",
           ".jsx", ".java", ".go", ".rs", ".cs", ".kt", ".swift", ".scala", ".php", ".dart",
           ".zig", ".v", ".sol"),
    "--": (".lean", ".hs", ".sql", ".lua", ".elm", ".ada"),
}


# (not .txt — CMakeLists.txt, requirements.txt — nor a `doc` folder: Go has src/go/doc)
_DOC_SUFFIXES = {".md", ".rst", ".adoc"}
_DOC_DIRS = {"docs"}
_LINT_PREFIXES = (".eslintrc", ".prettierrc", ".editorconfig", ".stylelintrc", ".flake8",
                  ".pylintrc", ".rubocop", ".golangci", ".markdownlint", ".codespellrc")


def generated(path: str) -> bool:
    """Not part of what a fix does: build output or a cache (what a solution script
    compiled or ran), documentation, or a linter's settings."""
    p = PurePosixPath(path.replace("\\", "/"))
    dirs = set(p.parts[:-1])
    return (bool(dirs & _GEN_DIRS) or p.suffix.lower() in _GEN_SUFFIXES
            or p.suffix.lower() in _DOC_SUFFIXES or bool(dirs & _DOC_DIRS)
            or p.name.startswith(_LINT_PREFIXES))


def _inert(pf, i: int, hunk, root: Path | None) -> bool:
    """The hunk changes nothing a program can observe: every line it adds or removes is
    blank, or comment / docstring through and through (judged with what the hunk's own
    lines show is open — a line inside a block comment or a triple-quoted string)."""
    suffix = PurePosixPath(pf.path).suffix.lower()
    if suffix in HASH or suffix in SLASH:
        if _all_quiet(hunk, suffix):
            return True
    else:
        marks = [m for m, exts in _COMMENT.items() if suffix in exts and m == "--"]
        changed = [ln.value.strip() for ln in hunk if ln.is_added or ln.is_removed]
        if all(not s or any(s.startswith(m) for m in marks) for s in changed):
            return True
    if suffix == ".py" and root is not None:
        return _same_python(pf, i, root)
    return False


def _all_quiet(hunk, suffix: str) -> bool:
    """Each changed line masks to nothing: the old side (context + removed lines) and the
    new side (context + added) are each read in order, so an open block comment or
    docstring carries from one line to the next."""
    for side in ("old", "new"):
        lines = [ln for ln in hunk
                 if not ((side == "old" and ln.is_added) or (side == "new" and ln.is_removed))]
        texts = [ln.value.rstrip("\n") for ln in lines]
        state = "*/" if suffix in SLASH and _starts_inside_block(texts) else None
        for ln, text in zip(lines, texts, strict=True):
            code, state = mask(text, suffix, state)
            if not (ln.is_added or ln.is_removed):
                continue
            if code.strip() or text.lstrip().startswith("#!"):   # code, or a shebang
                return False
    return True


def _starts_inside_block(texts: list[str]) -> bool:
    """The hunk begins partway through a ``/* ... */`` block: its first lines are `` * ``
    lines and a ``*/`` closes before any ``/*`` opens."""
    first = next((t.strip() for t in texts if t.strip()), "")
    if not first.startswith("*") or first.startswith("*/") and len(first) > 2:
        return False
    for t in texts:
        if "/*" in t:
            return False
        if "*/" in t:
            return True
    return False


def _same_python(pf, i: int, root: Path) -> bool:
    """The file with and without hunk ``i`` parses to the same program, docstrings aside."""
    base = root / pf.path
    try:
        lines = base.read_text(encoding="utf-8").splitlines(keepends=True) \
            if base.is_file() else []
        full = _apply(lines, list(pf))
        rest = _apply(lines, [h for j, h in enumerate(pf) if j != i])
        if full is None or rest is None:
            return False
        return _shape(full) == _shape(rest)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
        return False


def _apply(lines: list[str], hunks) -> str | None:
    out, pos = [], 0
    for h in hunks:
        start = h.source_start - 1 if h.source_length else h.source_start
        if start < pos:
            return None
        out += lines[pos:start]
        src = [ln.value for ln in h if ln.is_context or ln.is_removed]
        if [x.rstrip("\r\n") for x in lines[start:start + len(src)]] != \
                [x.rstrip("\r\n") for x in src]:
            return None                   # doesn't match the tree: can't tell, keep it
        out += [ln.value for ln in h if ln.is_context or ln.is_added]
        pos = start + h.source_length
    return "".join(out + lines[pos:])


def _shape(src: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]   # type: ignore[attr-defined]
    return ast.dump(tree, annotate_fields=False, include_attributes=False)


def _vcs_metadata(path: str) -> bool:
    """A file inside a ``.git`` directory, where a solution that runs git leaves changes."""
    return ".git" in path.replace("\\", "/").split("/")[:-1]


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
