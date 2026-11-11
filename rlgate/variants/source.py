"""Source-level variants: rewrite the code the gold fix touches, then diff it back to the base.

``revert`` and ``mutate`` edit the gold *diff* directly. Several L1 probes need to edit the
*code* instead — stub a changed function's body, make it return an always-equal object,
prepend an early ``os._exit(0)`` — which can't be expressed as a token swap in a hunk. This
module provides the three steps every such variant needs:

  1. ``gold_files(task)``: the touched files before and after the gold fix, plus which lines
     the fix added (applied once with the same zero-fuzz logic the sandboxes use);
  2. ``changed_functions(fc)``: the Python functions (stdlib ``ast``) the fix changed;
     ``replace_body`` rewrites one;
  3. ``diff_variant(task, files, …)``: diff the rewritten files against the pristine
     workspace into a ``Variant`` the engine and sandboxes apply unchanged.

All of it is a pure function of (workspace bytes, gold patch bytes): no RNG, no search.
Non-Python files simply yield no functions; probes skip them and report n/a.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rlgate.core.task import Task
from rlgate.sandbox._patch import PatchConflict, apply_zero_fuzz
from rlgate.variants.revert import Variant, _variant_id


@dataclass(frozen=True)
class FileChange:
    path: str                       # repo-relative (target path; source path if deleted)
    base: str | None                # content before the fix (None = file is created)
    gold: str | None                # content after the fix (None = file is deleted)
    added: frozenset[int]           # 1-based line numbers in `gold` the fix added


@dataclass(frozen=True)
class FuncSpan:
    path: str
    qualname: str                   # e.g. "Calc.add" — stable, for variant origins
    def_line: int                   # first line of the `def` (after decorators)
    body_start: int                 # first replaceable body line (after any docstring)
    end: int                        # last line of the function (inclusive)
    indent: str                     # indentation of the body statements
    is_method: bool = False         # defined directly in a class body
    decorated: bool = False         # has decorators (property, staticmethod, …)
    generator: bool = False         # contains yield (a stub changes its kind)
    is_async: bool = False
    returns_value: bool = False     # some `return <expr>` in its own body


# -- 1. the gold tree --------------------------------------------------------------------


def gold_files(task: Task) -> dict[str, FileChange]:
    """Touched files before/after the gold fix. Empty if there's no gold patch or it doesn't
    apply cleanly to the workspace on its own."""
    if task.gold_patch is None:
        return {}
    return dict(_gold_files(str(task.repo_dir), task.gold_patch.text))


@lru_cache(maxsize=64)
def _gold_files(repo_dir: str, patch_text: str) -> tuple[tuple[str, FileChange], ...]:
    from unidiff.patch import PatchSet

    ps = PatchSet(patch_text)
    repo = Path(repo_dir)
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-gold-"))
    try:
        for pf in ps:
            src = repo / pf.source_file.removeprefix("a/") if pf.source_file else None
            if src is not None and src.is_file():
                dst = tmp / pf.source_file.removeprefix("a/")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        try:
            apply_zero_fuzz(tmp, patch_text)
        except PatchConflict:
            return ()
        out: list[tuple[str, FileChange]] = []
        for pf in ps:
            path = pf.path
            base = _read(repo / path) if not pf.is_added_file else None
            gold = None if pf.is_removed_file else _read(tmp / path)
            if (base is None and not pf.is_added_file) or (gold is None and
                                                          not pf.is_removed_file):
                continue  # binary/undecodable — not rewritable as text
            added = frozenset(
                ln.target_line_no for h in pf for ln in h
                if ln.is_added and ln.target_line_no is not None
            )
            out.append((path, FileChange(path, base, gold, added)))
        return tuple(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# -- 2. changed functions ----------------------------------------------------------------


def changed_functions(fc: FileChange) -> list[FuncSpan]:
    """Outermost Python functions/methods in the post-fix file that contain an added line.
    Functions whose whole body sits on the ``def`` line are skipped (nothing to replace)."""
    if fc.gold is None or not fc.path.endswith(".py") or not fc.added:
        return []
    try:
        tree = ast.parse(fc.gold)
    except SyntaxError:
        return []
    spans: list[FuncSpan] = []
    _collect(tree, fc, fc.gold.splitlines(), "", spans)
    return spans


def _own_nodes(fn: ast.AST):
    """Nodes of a function's own body (not of nested functions, lambdas or classes)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))


def _collect(node: ast.AST, fc: FileChange, lines: list[str], prefix: str,
             out: list[FuncSpan], in_class: bool = False) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _collect(child, fc, lines, f"{prefix}{child.name}.", out, in_class=True)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = child.end_lineno or child.lineno
            if not any(child.lineno <= n <= end for n in fc.added):
                continue
            body = child.body
            first = body[0]
            if first.lineno == child.lineno:
                continue  # one-line def: `def f(): return 1`
            has_doc = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                       and isinstance(first.value.value, str))
            start_stmt = body[1] if has_doc and len(body) > 1 else first
            line = lines[start_stmt.lineno - 1]
            indent = line[: len(line) - len(line.lstrip())]
            own = list(_own_nodes(child))
            out.append(FuncSpan(
                fc.path, f"{prefix}{child.name}", child.lineno, start_stmt.lineno, end, indent,
                is_method=in_class, decorated=bool(child.decorator_list),
                generator=any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own),
                is_async=isinstance(child, ast.AsyncFunctionDef),
                returns_value=any(isinstance(n, ast.Return) and n.value is not None
                                  and not (isinstance(n.value, ast.Constant)
                                           and n.value.value is None) for n in own)))
            # outermost only: don't descend into nested defs of a matched function


def replace_body(text: str, span: FuncSpan, body: list[str]) -> str:
    """Replace lines ``body_start..end`` of ``text`` with ``body`` (indented to the span)."""
    lines = text.splitlines(keepends=True)
    new_body = [f"{span.indent}{b}\n" for b in body]
    tail = lines[span.end:]
    return "".join(lines[: span.body_start - 1] + new_body + tail)


# -- 3. diff back to a Variant -----------------------------------------------------------


def diff_variant(task: Task, files: dict[str, str | None], *, origin: str,
                 operator: str) -> Variant | None:
    """A ``Variant`` whose patch turns the pristine workspace into ``files`` (path → new
    content, None = delete). Base content is read from ``task.repo_dir``. Returns None when
    the rewrite changes nothing."""
    text = unified_diff(task.repo_dir, files)
    if not text:
        return None
    gold_sha = task.gold_patch.sha256 if task.gold_patch else "no-gold"
    return Variant(id=_variant_id(gold_sha, origin, operator), operator=operator,
                   patch_text=text, origin=origin)


def unified_diff(repo_dir: Path, files: dict[str, str | None]) -> str:
    """``git diff --no-index`` between the workspace's current files and ``files``.

    git gets the edge cases right (new/deleted files, missing trailing newline markers), and
    ``--no-prefix`` over ``a/``/``b/`` dirs yields ``a/<path>`` / ``b/<path>`` headers that
    ``apply_zero_fuzz`` (-p1) applies directly."""
    tmp = Path(tempfile.mkdtemp(prefix="rlgate-diff-"))
    try:
        (tmp / "a").mkdir()
        (tmp / "b").mkdir()
        for path, new in files.items():
            base = Path(repo_dir) / path
            if base.is_file():
                dst = tmp / "a" / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base, dst)
            if new is not None:
                dst = tmp / "b" / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(new, encoding="utf-8")
                if base.is_file():
                    shutil.copymode(base, dst)  # no spurious mode-change hunks
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--no-prefix", "--no-color", "--no-ext-diff",
             "--no-renames", "a", "b"],
            cwd=tmp, capture_output=True, text=True, check=False,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
        return proc.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def changed_python(task: Task, *, limit: int | None = None) -> list[tuple[FileChange, FuncSpan]]:
    """Every Python function the gold fix changes, file by file, in a stable order (test
    files excluded: the fix's own tests aren't the behaviour under test)."""
    from rlgate.variants.python import is_test_file

    out: list[tuple[FileChange, FuncSpan]] = []
    for path, fc in sorted(gold_files(task).items()):
        if is_test_file(path) or "/tests/" in f"/{path}":
            continue
        for span in changed_functions(fc):
            out.append((fc, span))
            if limit is not None and len(out) >= limit:
                return out
    return out


def with_rest_of_fix(task: Task, files: dict[str, str]) -> dict[str, str | None]:
    """The whole reference fix, with ``files`` rewritten (every other file the fix changes as
    the fix leaves it), ready for ``diff_variant``."""
    out: dict[str, str | None] = {p: fc.gold for p, fc in gold_files(task).items()}
    out.update(files)
    return out


def changed_sources(task: Task) -> list[FileChange]:
    """The non-test files the gold fix touches (what an agent would edit), stable order."""
    from rlgate.variants.python import is_test_file

    return [fc for p, fc in sorted(gold_files(task).items())
            if not is_test_file(p) and "/tests/" not in f"/{p}"]
