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
from collections.abc import Iterator
from pathlib import Path

from rlgate.core.task import Task

TEST_GLOBS = ("test_*.py", "*_test.py", "*_test.go", "*.test.ts", "*.test.js",
              "*.spec.ts", "*.spec.js")
PRUNE = {".git", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
_TARGET = re.compile(r"^\+\+\+ (?:b/)?(\S.*?)\s*$")
_MAX_SCRIPT = 5 * 2**20


def graded_tests(task: Task) -> Iterator[tuple[str, str]]:
    """(display path, test code) for what the grader runs."""
    added = test_patch_lines(task)
    if added is not None:
        for rel, lines in sorted(added.items()):
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


def test_patch_lines(task: Task) -> dict[str, list[str]] | None:
    """{test file: the lines its test patch adds}, from a patch embedded in a script the
    grader uploads (SWE-bench's eval script). None when there is none."""
    out: dict[str, list[str]] = {}
    for host, _ in task.grader.inject:
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
    return "\n".join(parts)


def runs_pytest(task: Task) -> bool | None:
    """True when the grader runs pytest, False when it names another runner and not
    pytest, None when its command doesn't say (a script in the workspace, `make test`)."""
    text = grader_text(task)
    if _PYTEST.search(text):
        return True
    return False if _OTHER_RUNNER.search(text) else None


def collected_paths(task: Task) -> set[str]:
    """Test files the grader names: listed test ids and the files a test patch touches."""
    ids = (*task.grader.fail_to_pass, *task.grader.pass_to_pass)
    paths = {i.split("::", 1)[0].split(" ", 1)[0] for i in ids if "::" in i or i.endswith(".py")}
    paths |= set(test_patch_lines(task) or {})
    return {p.lstrip("./") for p in paths if p}
