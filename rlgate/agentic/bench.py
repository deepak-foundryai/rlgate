"""The L3 agent's tools (agentic audits).

  - read-only tools over the pre-change repository: ``list_dir``, ``read_file`` (a 120-line
    window by default), ``search``, and structure from a cached symbol index
    (``agentic/index.py``): ``outline``, ``find_symbol``, ``find_refs``, ``read_symbol``, so
    the agent reads signatures and single definitions instead of whole files;
  - a candidate implementation it edits (``write_file``, ``edit_file``, ``show_changes``,
    ``discard_changes``), kept as a patch on the pre-change workspace and never written to
    the task's own files;
  - ``run``: a shell command in the sandbox with the candidate applied, as a solver would
    run it: the held-out tests are NOT copied in (that happens only when grading);
  - ``list_tests`` / ``read_test``: only for a judge that is explicitly allowed the tests.

Grading (the candidate against the real verifier) is not a free tool: each judge offers its
own grading tool with its own rules (``agentic/judges.py``). Every grading here goes
through ``core.grade.run_grader``, the same path every other check uses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from rlgate.agentic.index import Index, outline, parse
from rlgate.agentic.index import build as build_index
from rlgate.core.context import ProbeContext
from rlgate.core.grade import GradeResult, cached_grade
from rlgate.core.task import Task
from rlgate.sandbox._patch import PatchConflict
from rlgate.sandbox.base import ExecResult
from rlgate.variants.python import is_test_file
from rlgate.variants.source import unified_diff

RUN_LIMIT = 20                 # ``run`` calls per audit
RUN_TIMEOUT = 300
MAX_OUT = 6000                 # characters of a tool's answer
MAX_LINES = 400
WINDOW = 120                   # lines read_file shows when no end is given
_PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "site-packages",
          ".mypy_cache", ".pytest_cache", ".rlgate"}


class ToolError(Exception):
    """A tool call the agent got wrong; the message goes back to it."""


def fn(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props,
                       "required": required if required is not None else list(props)}}}


S = {"type": "string"}


def dispatch(obj: object, name: str, raw: str | dict) -> str:
    """Call ``obj.t_<name>`` with the JSON arguments; any mistake is told to the agent."""
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        f = getattr(obj, f"t_{name}", None)
        if f is None:
            raise ToolError(f"there is no tool {name!r}")
        out = str(f(**args))
    except json.JSONDecodeError as e:
        out = f"error: the arguments are not valid JSON ({e.msg})"
    except TypeError as e:
        out = f"error: wrong arguments for {name}: {e}"
    except ToolError as e:
        out = f"error: {e}"
    return out if len(out) <= MAX_OUT else out[:MAX_OUT] + "\n… (cut)"


class Bench:
    def __init__(self, task: Task, ctx: ProbeContext, *,
                 tests: dict[str, str] | None = None) -> None:
        self.task = task
        self.ctx = ctx
        self.repo = Path(task.repo_dir)
        self.files: dict[str, str] = {}       # the candidate: path → new content
        self.tests = tests                     # None: the tests are hidden from this judge
        self.runs = 0
        # where the held-out tests land at grading time: off limits for writing
        self._graded = [d.strip("/").rstrip("/") for _, d in task.grader.inject]
        self._index: Index | None = None

    @property
    def index(self) -> Index:
        if self._index is None:
            from rlgate.core.repro import state_dir
            self._index = build_index(self.repo, state_dir() / "index")
        return self._index

    # -- the candidate -------------------------------------------------------------------

    def patch(self) -> str:
        return unified_diff(self.repo, dict(self.files)) if self.files else ""

    def grade(self, patch: str | None) -> GradeResult | None:
        """The real verifier on the pre-change workspace plus ``patch``; None when the patch
        doesn't apply."""
        base = getattr(self.ctx, "baseline", None)
        try:
            return cached_grade(self.ctx.sandbox, self.task, patch,
                                deterministic=base is not None and not base.flaky_checks)
        except PatchConflict:
            return None

    def execute(self, patch: str | None, cmd: str) -> ExecResult | None:
        """``cmd`` in the solver's view of the workspace plus ``patch`` (no held-out tests)."""
        try:
            overlay = self.ctx.sandbox.overlay(patch or None)
        except PatchConflict:
            return None
        with overlay:
            return overlay.exec(cmd, timeout=RUN_TIMEOUT)

    # -- tool plumbing -------------------------------------------------------------------

    def specs(self) -> list[dict]:
        out = [
            fn("list_dir", "List a directory of the pre-change repository (your edits "
               "included). Directories end with /.", {"path": S}, []),
            fn("read_file", f"Read part of a file (your edited version if you changed it), "
               f"with line numbers: {WINDOW} lines from start_line unless you give end_line "
               f"(at most {MAX_LINES}). Prefer outline and read_symbol to whole files.",
               {"path": S, "start_line": {"type": "integer"},
                "end_line": {"type": "integer"}}, ["path"]),
            fn("outline", "The classes and functions of a file, or of every file in a "
               "directory, with signatures and line ranges.", {"path": S}),
            fn("find_symbol", "Where a class, function, method or constant is defined "
               "(name, or Class.method).", {"name": S}),
            fn("read_symbol", "The full source of one definition (name or Class.method; "
               "give path when the name is defined in several files).",
               {"name": S, "path": S}, ["name"]),
            fn("find_refs", "Where a name is used (whole-word matches, definitions "
               "included).", {"name": S, "path": S}, ["name"]),
            fn("search", "Search the repository's text files for a regular expression; "
               "returns path:line: text.", {"pattern": S, "path": S}, ["pattern"]),
            fn("write_file", "Create or replace a file of your candidate implementation "
               "(never a test file).", {"path": S, "content": S}),
            fn("edit_file", "Replace one exact occurrence of old_text with new_text in a "
               "file of your candidate.", {"path": S, "old_text": S, "new_text": S}),
            fn("show_changes", "Show your candidate as a diff against the pre-change "
               "repository.", {}, []),
            fn("discard_changes", "Throw away your candidate and start again from the "
               "pre-change repository.", {}, []),
            fn("run", "Run a shell command in a fresh copy of the workspace with your "
               f"candidate applied (no held-out tests; {RUN_TIMEOUT}s limit; at most "
               f"{RUN_LIMIT} runs). Use it to check your code imports and behaves as you "
               "intend.", {"command": S}),
        ]
        if self.tests is not None:
            out += [fn("list_tests", "List the test files the verifier runs.", {}, []),
                    fn("read_test", "Read one test file the verifier runs.", {"path": S})]
        return out

    def call(self, name: str, raw: str | dict) -> str:
        return dispatch(self, name, raw)

    # -- read-only repository tools ------------------------------------------------------

    def _path(self, path: str) -> str:
        p = PurePosixPath(path.strip() or ".")
        if p.is_absolute() or ".." in p.parts:
            raise ToolError(f"{path!r}: use a path relative to the repository root")
        s = p.as_posix()
        return "" if s == "." else s

    def _text(self, rel: str) -> str:
        if rel in self.files:
            return self.files[rel]
        f = self.repo / rel
        if not f.is_file():
            raise ToolError(f"{rel}: no such file")
        data = f.read_bytes()
        if b"\0" in data[:4096]:
            raise ToolError(f"{rel}: a binary file")
        return data.decode(errors="replace")

    def t_list_dir(self, path: str = ".") -> str:
        rel = self._path(path)
        d = self.repo / rel
        names = set()
        if d.is_dir():
            for c in d.iterdir():
                if c.name not in _PRUNE:
                    names.add(c.name + ("/" if c.is_dir() else ""))
        prefix = rel + "/" if rel else ""
        for f in self.files:
            if f.startswith(prefix):
                head = f[len(prefix):].split("/", 1)
                names.add(head[0] + ("/" if len(head) > 1 else ""))
        if not names:
            raise ToolError(f"{path}: no such directory")
        listed = sorted(names)
        more = f"\n… {len(listed) - 300} more" if len(listed) > 300 else ""
        return "\n".join(listed[:300]) + more

    def t_read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        lines = self._text(self._path(path)).splitlines()
        a = max(1, int(start_line))
        b = min(len(lines), int(end_line) if end_line else a + WINDOW - 1, a + MAX_LINES - 1)
        body = "\n".join(f"{i:5}| {lines[i - 1]}" for i in range(a, b + 1))
        tail = f"\n… {len(lines) - b} more lines" if b < len(lines) else ""
        return (body or "(empty file)") + tail

    # -- structure, from the index -------------------------------------------------------

    def _symbols(self, rel: str) -> list:
        if rel in self.files:                  # an edited file: parse the new text
            return parse(rel, self.files[rel])
        return self.index.in_file(rel)

    def t_outline(self, path: str) -> str:
        rel = self._path(path)
        if (self.repo / rel).is_file() or rel in self.files:
            return outline(self._symbols(rel)) or f"{rel}: no classes or functions found"
        prefix = rel + "/" if rel else ""
        files = sorted({f for f in self.index.files if f.startswith(prefix)} |
                       {f for f in self.files if f.startswith(prefix)})
        if not files:
            raise ToolError(f"{path}: no such file or directory")
        out, size = [], 0
        for f in files:
            syms = [s for s in self._symbols(f) if not s.parent]
            if not syms:
                continue
            block = f"{f}\n" + "\n".join(f"  {s.line}  {s.signature[:160]}" for s in syms)
            if size + len(block) > MAX_OUT - 200:
                out.append("… more files; outline a subdirectory")
                break
            out.append(block)
            size += len(block)
        return "\n".join(out) or "no classes or functions found"

    def t_find_symbol(self, name: str) -> str:
        found = self.index.defs(name.strip())
        if not found:
            return f"no definition of {name!r} (try find_refs or search)"
        return "\n".join(f"{s.path}:{s.line}-{s.end}  {s.kind}  "
                         f"{(s.parent + '.') if s.parent else ''}{s.name}  {s.signature}"
                         for s in found[:40])

    def t_read_symbol(self, name: str, path: str = "") -> str:
        found = self.index.defs(name.strip())
        if path:
            rel = self._path(path)
            found = [s for s in self._symbols(rel) if s.name == name.split(".")[-1]] or \
                [s for s in found if s.path == rel]
        if not found:
            raise ToolError(f"no definition of {name!r}; find_symbol or search first")
        if len({s.path for s in found}) > 1:
            raise ToolError(f"{name!r} is defined in several files ("
                            + ", ".join(sorted({s.path for s in found})[:8])
                            + "); give path")
        s = found[0]
        return self.t_read_file(s.path, s.line, min(s.end, s.line + MAX_LINES - 1))

    def t_find_refs(self, name: str, path: str = ".") -> str:
        word = name.strip().split(".")[-1]
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", word):
            raise ToolError("give one identifier")
        return self.t_search(rf"(?<![\w$]){re.escape(word)}(?![\w$])", path)

    def t_search(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        base = self._path(path)
        root = self.repo / base
        files = [root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []
        hits: list[str] = []
        seen: set[str] = set()
        for f in files:
            rel = f.relative_to(self.repo).as_posix()
            if any(part in _PRUNE for part in PurePosixPath(rel).parts) or not f.is_file() \
                    or f.stat().st_size > 512_000:
                continue
            seen.add(rel)
            self._grep(rel, rx, hits)
            if len(hits) >= 60:
                break
        for rel in self.files:
            if rel not in seen and rel.startswith(base) and len(hits) < 60:
                self._grep(rel, rx, hits)
        return "\n".join(hits[:60]) or "no match"

    def _grep(self, rel: str, rx: re.Pattern, hits: list[str]) -> None:
        try:
            text = self._text(rel)
        except ToolError:
            return
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(hits) >= 60:
                    return

    # -- the candidate -------------------------------------------------------------------

    def _writable(self, path: str) -> str:
        rel = self._path(path)
        if not rel:
            raise ToolError("give a file path")
        if is_test_file(rel) or "/tests/" in f"/{rel}/" or any(
                rel == d or rel.startswith(d + "/") for d in self._graded):
            raise ToolError(f"{rel} is a test file; candidates change the code, never the "
                            "tests")
        return rel

    def t_write_file(self, path: str, content: str) -> str:
        rel = self._writable(path)
        self.files[rel] = content
        return f"wrote {rel} ({len(content.splitlines())} lines)"

    def t_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        rel = self._writable(path)
        text = self._text(rel)
        n = text.count(old_text)
        if n != 1:
            raise ToolError(f"old_text occurs {n} times in {rel}; it must occur exactly once")
        self.files[rel] = text.replace(old_text, new_text)
        return f"edited {rel}"

    def t_show_changes(self) -> str:
        return self.patch() or "no changes yet"

    def t_discard_changes(self) -> str:
        self.files.clear()
        return "back to the pre-change repository"

    def t_run(self, command: str) -> str:
        if self.runs >= RUN_LIMIT:
            raise ToolError(f"the {RUN_LIMIT} runs are used up")
        self.runs += 1
        r = self.execute(self.patch(), command)
        if r is None:
            raise ToolError("your candidate doesn't apply to the workspace; show_changes")
        return (f"exit code {r.exit_code}\n--- stdout ---\n{r.stdout[-2500:]}\n"
                f"--- stderr ---\n{r.stderr[-2500:]}")

    # -- tests, for a judge allowed to see them ---------------------------------------------

    def t_list_tests(self) -> str:
        if self.tests is None:
            raise ToolError("the tests are hidden from this check")
        return "\n".join(self.tests) or "no test files"

    def t_read_test(self, path: str) -> str:
        if self.tests is None:
            raise ToolError("the tests are hidden from this check")
        if path not in self.tests:
            raise ToolError(f"{path}: not one of the test files (list_tests)")
        lines = self.tests[path].splitlines()
        return "\n".join(f"{i:5}| {x}" for i, x in enumerate(lines, 1))
