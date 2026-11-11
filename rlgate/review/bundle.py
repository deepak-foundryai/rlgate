"""What an L2 reviewer reads (static review).

First pass: the statement, the graded tests, the verifier command and the reference fix.
Second pass (stricter): the same with the reference fix taken out, plus the pre-change repo
(its file list and the files the tests and statement point at), so the reviewer can't fill a
gap in the statement from the solution. Never included in either: the post-change history,
and the author's notes (Harbor's ``task.toml`` [metadata] explanations, ``solution/``).

Every text a judge may quote is kept here verbatim, so a cited assertion can be checked
against the real files (``review/cite.py``).
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path

from rlgate.core.task import Task
from rlgate.review.evidence import Detail, details, search
from rlgate.review.evidence import render as render_evidence
from rlgate.review.evidence import text as evidence_text
from rlgate.variants.python import is_test_file

# How much of each part a judge sees. ~4 characters per token: about 100k tokens in all.
MAX_TESTS = 160_000
MAX_REFERENCE = 80_000
MAX_REPO_FILES = 100_000
MAX_TREE = 400                 # file names in the repo listing
MAX_FILE = 60_000              # one file larger than this is cut

_PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "site-packages",
          "dist", "build", ".mypy_cache", ".pytest_cache", ".rlgate", ".idea", ".vscode"}
_TEXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
         ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".bash", ".sql",
         ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".md", ".html", ".css",
         ".xml", ".mako", ".env", ".conf", ""}
_SKIP_NAMES = {"Dockerfile", ".dockerignore", "__init__.py", "uv.lock", "package-lock.json",
               "poetry.lock", "yarn.lock", "pnpm-lock.yaml", "go.sum", "Cargo.lock"}


@dataclass(frozen=True)
class Bundle:
    statement: str
    tests: dict[str, str]                 # display path → text: the graded tests
    verifier: str                         # the grading command, then the script it runs
    reference: str | None                 # the reference fix; None in the second pass
    tree: list[str] = field(default_factory=list)          # pre-change repo file names
    repo: dict[str, str] = field(default_factory=dict)     # relevant pre-change files
    cut: tuple[str, ...] = ()             # what was left out for size
    evidence: tuple[Detail, ...] = ()     # where the repo shows each tested detail (L2 Q1)

    @property
    def hidden(self) -> Bundle:
        """The second-pass view: the reference fix withheld."""
        return replace(self, reference=None)

    def source(self, name: str) -> str:
        """The verbatim text a quote from ``name`` must come from."""
        if name == "statement":
            return self.statement
        if name == "tests":
            return "\n".join(self.tests.values()) + "\n" + self.verifier
        if name == "verifier":
            return self.verifier
        if name == "reference":
            return self.reference or ""
        if name == "repo":
            return "\n".join(self.repo.values()) + "\n" + evidence_text(list(self.evidence))
        return ""

    def render(self, *, repo: bool) -> str:
        """The bundle as one prompt section, each file under its own header."""
        out = [_section("TASK STATEMENT (what the solver is given)", self.statement)]
        if repo:
            out.append(_section("PRE-CHANGE REPOSITORY: FILE LIST",
                                "\n".join(self.tree) or "(no files)"))
            for path, text in self.repo.items():
                out.append(_section(f"PRE-CHANGE FILE: {path}", text))
            if self.evidence:
                out.append(_section("REPOSITORY EVIDENCE FOR TESTED DETAILS",
                                    render_evidence(list(self.evidence))))
        for path, text in self.tests.items():
            out.append(_section(f"TEST FILE: {path}", text))
        out.append(_section("VERIFIER COMMAND (how the grade is decided)", self.verifier))
        if self.reference is not None:
            out.append(_section("REFERENCE FIX (the author's solution)", self.reference))
        if self.cut:
            out.append(_section("LEFT OUT FOR SIZE", "\n".join(self.cut)))
        return "\n\n".join(out)

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for part in (self.statement, self.verifier, self.reference or "",
                     *self.tests, *self.tests.values(), *self.tree, *self.repo,
                     *self.repo.values(), render_evidence(list(self.evidence))):
            h.update(part.encode(errors="replace"))
            h.update(b"\0")
        return h.hexdigest()


def _section(title: str, body: str) -> str:
    return f"===== {title} =====\n{body.rstrip()}\n===== END ====="


# -- building it ---------------------------------------------------------------------------

def build(task: Task) -> Bundle:
    cut: list[str] = []
    verifier, script = _verifier(task)
    # a shell verifier is shown once, under the command; a Python grader is a test too
    tests = _tests(task, skip={script} if script and not script.endswith(".py") else set(),
                   cut=cut)
    statement = task.instruction or ""
    reference = _reference(task, cut)
    tree = _tree(task.repo_dir)
    if len(tree) > MAX_TREE:
        cut.append(f"repo file list: {len(tree) - MAX_TREE} more file names")
        tree = tree[:MAX_TREE]
    repo = _relevant(task.repo_dir, statement, tests, cut)
    return Bundle(statement, tests, verifier, reference, tree, repo, tuple(cut),
                  _evidence(task.repo_dir, statement, tests))


def _evidence(root: Path, statement: str, tests: dict[str, str]) -> tuple[Detail, ...]:
    """Tested details the statement doesn't name, and where the pre-change repo shows them
    (the graded tests themselves excluded: a test can't justify itself)."""
    if not root.is_dir() or not tests:
        return ()
    terms = details(tests, statement)
    if not terms:
        return ()
    skip = {hashlib.sha1(t.encode(errors="replace")).hexdigest() for t in tests.values()}
    files = [(p, rel) for p, rel in _walk(root)
             if p.suffix in _TEXT and p.name not in _SKIP_NAMES]
    return tuple(search(root, terms, skip_hashes=skip, files=files))


def _read(p: Path) -> str | None:
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode(errors="replace")


def _walk(root: Path):
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _PRUNE for part in rel.parts) or not p.is_file():
            continue
        yield p, rel.as_posix()


def _tree(root: Path) -> list[str]:
    return [rel for _, rel in _walk(root)]


def _verifier(task: Task) -> tuple[str, str | None]:
    """The grading command and, when it runs a script from the task, that script."""
    g = task.grader
    how = {"exit_code": "pass = the command exits 0",
           "report": "pass = the test report has no failure and at least one pass",
           "both": "pass = the command exits 0 and the test report is clean",
           "reward_file": "the reward is read from " + ", ".join(g.reward_files or ("a file",))
           }.get(g.verdict, g.verdict)
    head = f"$ {g.run}\n# {how}"
    for host, path in _scripts(task):
        text = _read(host)
        if text is not None:
            return f"{head}\n\n# {path}:\n{text}", str(host.resolve())
    return head, None


def _scripts(task: Task):
    """Host files the grading command runs (``bash /tests/test.sh`` → tests/test.sh)."""
    try:
        words = shlex.split(task.grader.run)
    except ValueError:
        words = task.grader.run.split()
    roots = [(Path(h), c.rstrip("/")) for h, c in task.grader.inject]
    for w in words:
        if not re.search(r"\.(sh|py|bash)$", w):
            continue
        for host, cont in roots:
            if w.startswith(cont + "/"):
                yield host / w[len(cont) + 1:], w
        for base, _ in task.test_roots:
            cand = base / w.lstrip("/")
            if cand.is_file():
                yield cand, w
            cand = base / Path(w).name
            if cand.is_file():
                yield cand, w


def _tests(task: Task, *, skip: set[str], cut: list[str]) -> dict[str, str]:
    """Graded tests: test files in the workspace, and every text file the grader brings
    (held-out tests, their helpers and data) except copies of workspace files."""
    ws = {rel for _, rel in _walk(task.repo_dir)} if task.repo_dir.is_dir() else set()
    seen = {_hash(t) for p, _ in _walk(task.repo_dir) if (t := _read(p)) is not None} \
        if task.repo_dir.is_dir() else set()
    out: dict[str, str] = {}
    size = 0
    for root, prefix in task.test_roots:
        own = root.resolve() == task.repo_dir.resolve()
        for p, rel in _walk(root):
            if str(p.resolve()) in skip or p.name in _SKIP_NAMES or p.suffix not in _TEXT:
                continue
            if own and not is_test_file(rel):
                continue
            text = _read(p)
            if text is None or not text.strip():
                continue
            if not own and (_hash(text) in seen or _mirrors(rel, ws)):
                continue                   # a copy of the app the grader builds on (scaffold)
            name = prefix + rel
            if size + len(text) > MAX_TESTS:
                cut.append(f"test file {name} ({len(text)} characters)")
                continue
            out[name] = text if len(text) <= MAX_FILE else text[:MAX_FILE]
            size += len(out[name])
    return out


def _mirrors(rel: str, workspace: set[str]) -> bool:
    """``scaffold/helpers/x.py`` mirrors the workspace's ``helpers/x.py``."""
    parts = rel.split("/")
    return any("/".join(parts[i:]) in workspace for i in range(1, len(parts)))


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode(errors="replace")).hexdigest()


def _reference(task: Task, cut: list[str]) -> str | None:
    text = task.gold_patch.text if task.gold_patch is not None else None
    if text is None and task.oracle is not None:
        text = _read(Path(task.oracle[0]) / task.oracle[1])
    if text is not None and len(text) > MAX_REFERENCE:
        cut.append(f"reference fix after {MAX_REFERENCE} characters")
        text = text[:MAX_REFERENCE]
    return text


_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
_PATHISH = re.compile(r"[\w./-]+\.(?:py|js|ts|go|rs|java|rb|sh|sql|json|ya?ml|toml)\b")
_TICKED = re.compile(r"`([^`\n]{2,80})`")


def _relevant(root: Path, statement: str, tests: dict[str, str], cut: list[str]
              ) -> dict[str, str]:
    """Pre-change files the tests import or name, and files the statement names: what a
    solver would open first. Chosen without the reference fix."""
    if not root.is_dir():
        return {}
    files = {rel: p for p, rel in _walk(root) if p.suffix in _TEXT and p.name not in
             _SKIP_NAMES and not is_test_file(rel)}
    wanted: list[str] = []
    blob = "\n".join(tests.values())
    for m in _IMPORT.finditer(blob):
        mod = (m.group(1) or m.group(2)).replace(".", "/")
        for cand in (mod + ".py", mod + "/__init__.py"):
            if cand in files:
                wanted.append(cand)
    for text in (blob, statement):
        for m in _PATHISH.finditer(text):
            tail = m.group(0).lstrip("./")
            wanted += [rel for rel in files if rel == tail or rel.endswith("/" + tail)]
    for m in _TICKED.finditer(statement):
        word = m.group(1).strip().strip("/").split("/")[-1]
        if re.fullmatch(r"[A-Za-z_][\w.]*", word) and len(word) >= 4:
            stem = word.split(".")[0]
            wanted += [rel for rel in files if Path(rel).stem == stem]
    out: dict[str, str] = {}
    size = 0
    for rel in dict.fromkeys(wanted):
        text = _read(files[rel])
        if text is None:
            continue
        if size + min(len(text), MAX_FILE) > MAX_REPO_FILES:
            cut.append(f"pre-change file {rel}")
            continue
        out[rel] = text[:MAX_FILE]
        size += len(out[rel])
    return out
