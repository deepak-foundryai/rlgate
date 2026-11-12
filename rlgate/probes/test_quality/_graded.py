"""The test code a grader actually runs, for the static test-quality checks.

A repository holds many test files the grader never runs (SWE-bench grades only what the
row's test patch touches; Harbor grades its held-out ``tests/``). Scanning all of them made
"a test pins an exact call count" fire on files no grading ever reads. So, in order:

1. the lines a test patch adds (SWE-bench rows: the patch inside the uploaded eval script);
2. the held-out test folders the grader brings in;
3. the workspace's own test files, when that is all there is.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from rlgate.core.task import Task

TEST_GLOBS = ("test_*.py", "*_test.py", "*_test.go", "*.test.ts", "*.test.js",
              "*.spec.ts", "*.spec.js")
PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
_TARGET = re.compile(r"^\+\+\+ (?:b/)?([^\t\n]*?)\s*(?:\t.*)?$")    # no diff -u timestamp
_MAX_SCRIPT = 5 * 2**20


def graded_tests(task: Task) -> Iterator[tuple[str, str]]:
    """(display path, test code) for what the grader runs."""
    added = test_patch_lines(task)
    if added is not None:
        for rel, lines in sorted(added.items()):
            if _is_test(rel):
                yield rel, "\n".join(lines)
        return
    roots = task.test_roots
    held = roots[1:] if len(roots) > 1 else roots
    for root, prefix in held:
        for p in _test_files(root):
            try:
                yield prefix + p.relative_to(root).as_posix(), p.read_text(errors="ignore")
            except OSError:
                continue


def test_patch_lines(task: Task, *, include_removed: bool = False
                     ) -> dict[str, list[str]] | None:
    """{test file: the lines its test patch adds}, from a patch embedded in a script the
    grader uploads (SWE-bench's eval script). None when there is none."""
    out: dict[str, list[str]] = {}
    # the grader's uploads, and the held-out test folders (Harbor's tests/ holds DeepSWE's
    # test.patch; ``inject`` is only filled in once a sandbox stages the task)
    hosts = dict.fromkeys([str(h) for h, _ in task.grader.inject]
                          + [str(r) for r, _ in task.test_roots[1:]])
    for host in hosts:
        base = Path(host)
        files = [base] if base.is_file() else (base.rglob("*") if base.is_dir() else [])
        for f in files:
            try:
                if not f.is_file() or f.stat().st_size > _MAX_SCRIPT:
                    continue
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            if "diff --git" not in text:
                continue
            current: str | None = None
            for line in text.splitlines():
                if include_removed and line.startswith("--- a/"):
                    out.setdefault(line[6:].split("\t", 1)[0].rstrip(), [])
                m = _TARGET.match(line)
                if m:
                    current = None if m.group(1) == "/dev/null" else m.group(1)
                    if current is not None:
                        out.setdefault(current, [])
                elif current is not None and line.startswith("+"):
                    out[current].append(line[1:])
                elif line.startswith(("diff --git", "EOF")):
                    current = None
    return out or None


def _is_test(rel: str) -> bool:
    """A test file (by name, or under a test folder) — not the runner script a patch ships."""
    p = PurePosixPath(rel)
    return (any(fnmatch(p.name, g) for g in TEST_GLOBS) or p.name == "conftest.py"
            or any(d in ("tests", "test", "testing", "__tests__", "spec") for d in p.parts[:-1])
            ) and p.suffix not in (".sh", ".bash")


def _test_files(root: Path) -> Iterator[Path]:
    seen: set[Path] = set()
    for glob in TEST_GLOBS:
        for p in root.rglob(glob):
            if p in seen or any(part in PRUNE for part in p.relative_to(root).parts):
                continue
            if p.is_file():
                seen.add(p)
                yield p


_PYTEST = re.compile(r"\bpy\.?test\b")
# a runner named in the grader that is not pytest: django's runtests.py, sympy's bin/test, ...
_OTHER_RUNNER = re.compile(r"runtests\.py|\bbin/test\b|\bgo test\b|\bcargo test\b|\bnpm\b|"
                           r"\byarn\b|\bpnpm\b|\bjest\b|\bvitest\b|\bmocha\b|\bmvn\b|\bgradle\b|"
                           r"\bctest\b|\brspec\b|\bphpunit\b|\bdotnet test\b|\bunittest\b|"
                           r"\bnose(tests)?\b|\blake\b")


def grader_text(task: Task) -> str:
    """The grader's command plus the scripts it uploads (eval.sh, Harbor's test.sh)."""
    parts = [task.grader.run]
    for root, _ in task.test_roots[1:]:
        for f in sorted(root.glob("*.sh"))[:10]:
            try:
                if f.stat().st_size <= _MAX_SCRIPT:
                    parts.append(f.read_text(errors="ignore"))
            except OSError:
                continue
    # a runner script the held-out test patch adds (DeepSWE's test.sh, run as
    # `bash /app/test.sh base|new`): its pytest lines say what is collected
    for rel, lines in sorted((test_patch_lines(task) or {}).items()):
        if rel.endswith((".sh", ".bash")):
            parts.append("\n".join(lines))
    return "\n".join(parts)


def runs_pytest(task: Task) -> bool | None:
    """True when the grader runs pytest, False when it names another runner and not
    pytest, None when its command doesn't say (a script in the workspace, `make test`)."""
    text = grader_text(task)
    if _PYTEST.search(text):
        return True
    return False if _OTHER_RUNNER.search(text) else None


def collected_paths(task: Task) -> set[str] | None:
    """Where pytest starts collecting, as repo-relative paths (files or folders): the
    paths the grader passes to pytest, listed test ids, and the files a test patch touches.
    None when some pytest run names no path (it collects from where it runs: everything).
    Paths outside the repository (Harbor's /tests) are left out: pytest doesn't walk the
    repository for those."""
    ids = (*task.grader.fail_to_pass, *task.grader.pass_to_pass)
    paths = {i.split("::", 1)[0].split(" ", 1)[0] for i in ids if "::" in i or i.endswith(".py")}
    paths |= {p for p in test_patch_lines(task) or {} if _is_test(p)}
    known = bool(paths)
    for words in _commands(grader_text(task)):
        at = next((k for k, w in enumerate(words)
                   if _PYTEST.fullmatch(w.rsplit("/", 1)[-1])
                   or (w == "-m" and k + 1 < len(words) and words[k + 1] == "pytest")), None)
        if at is None or not _launches(words[:at]):
            continue        # `require_cmd pytest`, `echo pytest`: pytest isn't what runs
        rest = words[at + 1:]
        args = [w for k, w in enumerate(rest) if w != "pytest" and not w.startswith("-")
                and "=" not in w and not (k and rest[k - 1] in _VALUE_OPTS)
                and ("/" in w or w.endswith(".py") or "::" in w or w in _TEST_DIRS
                     or (task.repo_dir / w).is_dir())]
        if not args:
            return None
        known = True
        paths |= {w.split("::", 1)[0] for w in args}
    if not known:
        return None                  # the grader doesn't say: assume everything
    out = set()
    for p in paths:
        q = _repo_path(task, p)
        if q is not None and not q.startswith("/"):
            out.add(q)
    return out


_TEST_DIRS = {"tests", "test", "testing"}
# pytest options whose value is the next word, not a path to collect
_VALUE_OPTS = {"-o", "-W", "-p", "-k", "-m", "-c", "-n", "-r", "--tb", "--rootdir",
               "--confcutdir", "--deselect", "--ignore", "--ignore-glob", "--junitxml",
               "--junit-xml", "--cov", "--durations", "--maxfail", "--timeout", "--log-level",
               "--basetemp", "--override-ini", "--import-mode", "--ctrf", "--json-report-file"}
# what may come before pytest on a line that runs it: `python -m`, `uv run`, `timeout 600`
_LAUNCHERS = re.compile(r"(?:.*/)?(?:python[\d.]*|uv|poetry|pipenv|pdm|hatch|rye|run|coverage|"
                        r"timeout|xvfb-run|env|nice|stdbuf|-m|-[\w-]+|\d+[smh]?)$")


def _launches(before: list[str]) -> bool:
    """The words before pytest only start it (nothing, or a launcher and its flags)."""
    return all(_LAUNCHERS.match(w) for w in before)
_SEP = re.compile(r"&&|\|\||(?<!\\);|\|")
_DIFF_LINE = re.compile(r"^(?:[+-]|@@ |diff --git |index [0-9a-f]+\.\.)")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w]*)\1")


def _commands(text: str) -> list[list[str]]:
    """The grader's shell commands as word lists (continued lines joined)."""
    out = []
    end: str | None = None                 # inside a heredoc body (a patch, a file): not commands
    for line in re.sub(r"\\\n", " ", text).splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        if _DIFF_LINE.match(line):
            continue                       # a diff line outside a heredoc: `+ import pytest`
        m = _HEREDOC.search(line)
        if m:
            end = m.group(2)
            line = line[:m.start()]
        for part in _SEP.split(line):
            try:
                words = shlex.split(part, comments=True)
            except ValueError:
                words = part.split()
            while words and ("=" in words[0] and not words[0].startswith("-")
                             or words[0] in ("sudo", "exec", "time", "then", "do", "else")):
                words = words[1:]
            if words:
                out.append(words)
    return out


def _repo_path(task: Task, p: str, *, var_is_root: bool = False) -> str | None:
    """``p`` relative to the repository root: the workdir prefix dropped, a leading
    ``$VAR/`` (a copy of the repo, as Harbor graders deploy) taken as the root. None for
    a bare variable (unknown)."""
    p = p.strip("\"'")
    m = re.match(r"^\$\{?\w+\}?(/|$)", p)
    if m:
        p = p[m.end():]
        if not p:
            return "" if var_is_root else None
    if p.startswith("$"):
        return None
    roots = {"/testbed", "/app", "/workspace", "/repo", "/code", "/src"}
    for d in (task.workspace_env.workdir if task.workspace_env else None, task.grader.cwd):
        if d:
            roots.add(d.rstrip("/"))
    for r in sorted(roots, key=len, reverse=True):
        if p == r:
            return ""
        if p.startswith(r + "/"):
            return p[len(r) + 1:]
    return p.removeprefix("./").rstrip("/")


class Removal:
    """What the grader deletes or restores before the tests run, from its own commands:
    ``rm`` and ``git checkout/restore`` of paths, ``find <dir> -name X -delete``."""

    def __init__(self) -> None:
        self.paths: set[str] = set()                  # repo-relative files/folders/globs
        self.named: set[tuple[str, str]] = set()      # (folder, file-name glob)

    def covers(self, rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        for q in self.paths:
            if rel == q or rel.startswith(q + "/") or ("*" in q and fnmatch(rel, q)):
                return True
        return any((not d or rel.startswith(d + "/")) and fnmatch(name, n)
                   for d, n in self.named)


def grader_removes(task: Task) -> Removal:
    r = Removal()
    # a held-out test patch is applied over the candidate's tree (git apply fails, or the
    # grader resets the file first, when a candidate edited it): what it touches is restored
    r.paths |= set(test_patch_lines(task, include_removed=True) or {})
    for words in _commands(grader_text(task)):
        cmd, args = words[0].rsplit("/", 1)[-1], words[1:]
        if cmd == "rm":
            targets = [w for w in args if not w.startswith("-")]
        elif cmd == "git" and args and args[0] in ("checkout", "restore"):
            rest = args[1:]
            if "--" in rest:
                targets = rest[rest.index("--") + 1:]
            else:                    # `git restore p`; `git checkout <ref> p` (not a branch)
                plain = [w for w in rest if not w.startswith("-")]
                targets = plain if args[0] == "restore" else plain[1:]
        elif cmd == "find" and ("-delete" in args or ("-exec" in args and "rm" in args)):
            starts = []
            for w in args:
                if w.startswith(("-", "(", "!", "\\")):
                    break
                starts.append(w)
            names = [args[k + 1] for k, w in enumerate(args[:-1]) if w in ("-name", "-iname")]
            for d in starts or ["."]:
                folder = _repo_path(task, d, var_is_root=True)
                if folder is None or folder.startswith("/"):
                    continue
                folder = "" if folder == "." else folder
                r.named |= {(folder, n) for n in names}
            continue
        else:
            continue
        for t in targets:
            q = _repo_path(task, t)
            if q and not q.startswith("/"):
                r.paths.add(q)
    return r
