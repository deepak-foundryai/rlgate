"""Files a solution script writes as the output of running something, not as the fix.

A Harbor gold patch is the workspace diff after ``solve.sh`` runs, so it also holds what the
script produced (``python run.py --out /app/results.json``). When neither the instruction
nor the grader's tests name such a file, removing or mutating it says nothing about the fix.
What the script writes itself (``cat > x.py <<EOF``, ``echo … > f``, ``cp``) is the fix, and
a run output the task asks for (``answer.txt``) is too: both stay.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

from rlgate.core.task import Task

_OUT_FLAGS = {"--out", "--output", "--output-file", "--out-file", "-o", "--save"}
_REDIRECT = re.compile(r"(?:^|\s)>>?\s*([\w./$\"'{}-]+)")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_AUTHORS = {"cat", "echo", "printf", "tee", "cp", "mv", "install", "ln", "sed", "envsubst"}
_MAX = 200_000


def solution_outputs(task: Task) -> set[str]:
    """Repo-relative paths the oracle script writes by running a program, when neither the
    instruction nor the held-out tests name them."""
    oracle = getattr(task, "oracle", None)
    if not oracle:
        return set()
    try:
        text = (Path(oracle[0]) / oracle[1]).read_text(errors="ignore")[:_MAX]
    except OSError:
        return set()
    found = _run_outputs(text)
    if not found:
        return set()
    from rlgate.probes.test_quality._graded import _repo_path
    named = _named_text(task)
    out = set()
    for p in found:
        q = _repo_path(task, p.strip("\"'"))
        if not q or q.startswith("/") or "$" in q or "{" in q:
            continue
        name = PurePosixPath(q).name.lower()
        if name in named:
            continue                    # the task asks for this file: it is the answer
        if text.lower().count(name) > 1:
            continue                    # the solution reads it back: part of what it builds
        out.add(q)
    return out


def _run_outputs(text: str) -> list[str]:
    found: list[str] = []
    end: str | None = None
    for line in re.sub(r"\\\n", " ", text).splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        h = _HEREDOC.search(line)
        if h:
            end = h.group(2)
            continue                    # `cat > f <<EOF`: the script authors f itself
        for part in re.split(r"&&|\|\||;|\|", line):
            try:
                words = shlex.split(part, comments=True)
            except ValueError:
                continue
            while words and "=" in words[0] and not words[0].startswith("-"):
                words = words[1:]
            if not words or words[0].rsplit("/", 1)[-1] in _AUTHORS:
                continue
            for k, w in enumerate(words):
                if w in _OUT_FLAGS and k + 1 < len(words):
                    found.append(words[k + 1])
                elif "=" in w and w.split("=", 1)[0] in _OUT_FLAGS:
                    found.append(w.split("=", 1)[1])
            found += [m.group(1) for m in _REDIRECT.finditer(part.split(" #", 1)[0])]
    return [f for f in found if f and not f.startswith(("&", "-")) and "/dev/" not in f
            and re.search(r"[A-Za-z]", f)]


def _named_text(task: Task) -> str:
    """The instruction and the held-out tests, lower-cased: where a required file is named."""
    parts = [task.instruction or ""]
    for root, _ in task.test_roots[1:]:
        for f in list(root.rglob("*"))[:500]:
            try:
                if f.is_file() and f.stat().st_size <= _MAX:
                    parts.append(f.read_text(errors="ignore"))
            except OSError:
                continue
    return "\n".join(parts).lower()
