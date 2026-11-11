"""A symbol index of the pre-change repository, for the L3/L4 agents.

Large repositories don't fit in a prompt, and reading whole files turn after turn is what
makes agent runs expensive. The agents navigate by structure instead: an outline of a file
or directory (classes and functions with their signatures and line ranges), where a name is
defined, where it is used, and the body of one definition. Research on code agents points
the same way (repo maps, skeleton views, 100-line windows beat whole files).

Built once per repository content and cached on disk under the state directory, keyed by a
fingerprint of the file list, sizes and modification times. Python is parsed with ``ast``;
other languages with line patterns, which find definitions well enough to navigate by.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "site-packages",
         ".mypy_cache", ".pytest_cache", ".rlgate", "dist", "build", ".idea", ".vscode",
         "vendor", "target", ".next", "coverage"}
MAX_FILE = 1_000_000           # bytes; larger files are listed but not parsed
MAX_FILES = 60_000             # files parsed at most

_LANG = {".py": "python", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js",
         ".go": "go", ".rs": "rust", ".java": "java", ".kt": "java", ".cs": "java",
         ".scala": "java", ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cc": "c",
         ".cpp": "c", ".hpp": "c", ".swift": "swift", ".sh": "sh"}

_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "js": [("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+"
                                r"([A-Za-z_$][\w$]*)")),
           ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?"
                                   r"\s+([A-Za-z_$][\w$]*)")),
           ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"
                                   r"\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*"
                                   r"(?::[^=]+)?=>")),
           ("method", re.compile(r"^\s+(?:public\s+|private\s+|protected\s+|static\s+|"
                                 r"async\s+|readonly\s+)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*"
                                 r"(?::[^{]+)?\{\s*$")),
           ("type", re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+"
                               r"([A-Za-z_$][\w$]*)"))],
    "go": [("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
           ("type", re.compile(r"^type\s+([A-Za-z_]\w*)"))],
    "rust": [("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+"
                                     r"([A-Za-z_]\w*)")),
             ("type", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl)"
                                 r"\s+([A-Za-z_]\w*)"))],
    "java": [("class", re.compile(r"^\s*(?:[\w@]+\s+)*(?:class|interface|enum|record|object)"
                                  r"\s+([A-Za-z_]\w*)")),
             ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|final|"
                                   r"abstract|override|suspend|fun|async|virtual|internal)"
                                   r"\s+)+[\w<>\[\],? ]*?\b([A-Za-z_]\w*)\s*\("))],
    "ruby": [("class", re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)")),
             ("method", re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"))],
    "php": [("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+"
                                 r"([A-Za-z_]\w*)")),
            ("function", re.compile(r"^\s*(?:(?:public|private|protected|static)\s+)*"
                                    r"function\s+&?([A-Za-z_]\w*)"))],
    "c": [("function", re.compile(r"^[A-Za-z_][\w\s\*&:<>,]*?\b([A-Za-z_]\w*)\s*\([^;]*$")),
          ("type", re.compile(r"^\s*(?:typedef\s+)?(?:struct|class|enum|union)\s+"
                              r"([A-Za-z_]\w*)"))],
    "swift": [("function", re.compile(r"^\s*(?:\w+\s+)*func\s+([A-Za-z_]\w*)")),
              ("class", re.compile(r"^\s*(?:\w+\s+)*(?:class|struct|enum|protocol|extension)"
                                   r"\s+([A-Za-z_]\w*)"))],
    "sh": [("function", re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?"))],
}
_C_SKIP = {"if", "for", "while", "switch", "return", "sizeof", "else", "case"}


@dataclass
class Symbol:
    name: str
    kind: str                     # class | function | method | type
    path: str
    line: int
    end: int
    signature: str
    parent: str = ""              # the enclosing class, for methods


@dataclass
class Index:
    root: str
    files: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    _by_name: dict[str, list[Symbol]] = field(default_factory=dict, repr=False)
    _by_path: dict[str, list[Symbol]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        self._by_name, self._by_path = {}, {}
        for s in self.symbols:
            self._by_name.setdefault(s.name, []).append(s)
            self._by_path.setdefault(s.path, []).append(s)

    def defs(self, name: str) -> list[Symbol]:
        """Definitions of ``name`` (``Class.method`` narrows to one class)."""
        if "." in name:
            parent, _, leaf = name.rpartition(".")
            return [s for s in self._by_name.get(leaf, []) if s.parent.endswith(parent)]
        return list(self._by_name.get(name, []))

    def in_file(self, path: str) -> list[Symbol]:
        return list(self._by_path.get(path, []))

    def to_json(self) -> str:
        return json.dumps({"root": self.root, "files": self.files,
                           "symbols": [asdict(s) for s in self.symbols]})

    @classmethod
    def from_json(cls, text: str) -> Index:
        d = json.loads(text)
        return cls(d["root"], d["files"], [Symbol(**s) for s in d["symbols"]])


# -- building -----------------------------------------------------------------------------

def walk(root: Path):
    """(host path, repo-relative path) of every file, pruned dirs skipped, sorted."""
    if not root.is_dir():
        return
    stack = [root]
    out = []
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name in PRUNE:
                continue
            if p.is_dir() and not p.is_symlink():
                stack.append(p)
            elif p.is_file():
                out.append((p, p.relative_to(root).as_posix()))
    yield from sorted(out, key=lambda x: x[1])


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p, rel in walk(root):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{rel}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
    return h.hexdigest()[:20]


def build(root: str | Path, cache_dir: Path | None = None) -> Index:
    """The index of ``root``, from the cache when the repository hasn't changed."""
    root = Path(root)
    key = fingerprint(root)
    if cache_dir is not None:
        f = cache_dir / f"{key}.json"
        try:
            return Index.from_json(f.read_text())
        except (OSError, ValueError, KeyError, TypeError):
            pass
    files: list[str] = []
    symbols: list[Symbol] = []
    for n, (p, rel) in enumerate(walk(root)):
        files.append(rel)
        if n >= MAX_FILES or PurePosixPath(rel).suffix not in _LANG:
            continue
        try:
            if p.stat().st_size > MAX_FILE:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        symbols += parse(rel, data.decode(errors="replace"))
    idx = Index(str(root), files, symbols)
    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{key}.json").write_text(idx.to_json())
        except OSError:
            pass
    return idx


def parse(rel: str, text: str) -> list[Symbol]:
    lang = _LANG.get(PurePosixPath(rel).suffix)
    if lang == "python":
        try:
            return _python(rel, text)
        except (SyntaxError, ValueError, RecursionError):
            return _lines(rel, text, [("class", re.compile(r"^\s*class\s+(\w+)")),
                                      ("function", re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"))])
    if lang is None:
        return []
    return _lines(rel, text, _PATTERNS[lang], c_like=lang == "c")


def _python(rel: str, text: str) -> list[Symbol]:
    tree = ast.parse(text)
    lines = text.splitlines()
    out: list[Symbol] = []

    def sig(node: ast.AST) -> str:
        first = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""   # type: ignore[attr-defined]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            body_line = node.body[0].lineno if node.body else node.lineno
            head = " ".join(x.strip() for x in lines[node.lineno - 1:max(node.lineno,
                                                                         body_line - 1)])
            return (head or first)[:240]
        return first[:240]

    def visit(body: list[ast.stmt], parent: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                out.append(Symbol(node.name, "class", rel, node.lineno,
                                  node.end_lineno or node.lineno, sig(node), parent))
                visit(node.body, f"{parent}.{node.name}" if parent else node.name)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                out.append(Symbol(node.name, "method" if parent else "function", rel,
                                  node.lineno, node.end_lineno or node.lineno, sig(node),
                                  parent))
            elif isinstance(node, ast.Assign) and not parent:
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        out.append(Symbol(t.id, "constant", rel, node.lineno,
                                          node.end_lineno or node.lineno,
                                          lines[node.lineno - 1].strip()[:240]))

    visit(tree.body, "")
    return out


def _lines(rel: str, text: str, pats: list[tuple[str, re.Pattern]], *,
           c_like: bool = False) -> list[Symbol]:
    lines = text.splitlines()
    found: list[tuple[int, str, str, int]] = []         # line, name, kind, indent
    for i, line in enumerate(lines, 1):
        for kind, rx in pats:
            m = rx.match(line)
            if not m:
                continue
            name = m.group(1)
            if c_like and (name in _C_SKIP or line.startswith((" ", "\t", "#", "/", "*"))):
                continue
            if kind == "method" and name in _C_SKIP | {"function"}:
                continue
            found.append((i, name, kind, len(line) - len(line.lstrip())))
            break
    out: list[Symbol] = []
    for n, (i, name, kind, indent) in enumerate(found):
        end = len(lines)
        for j, _, _, ind in found[n + 1:]:
            if ind <= indent:
                end = j - 1
                break
        parent = ""
        for pi, pname, pkind, pind in reversed(found[:n]):
            if pkind in ("class", "type") and pind < indent:
                parent = pname
                break
        out.append(Symbol(name, kind, rel, i, max(i, end), lines[i - 1].strip()[:240],
                          parent))
    return out


# -- views --------------------------------------------------------------------------------

def outline(symbols: list[Symbol]) -> str:
    """Signatures with line ranges, methods indented under their class."""
    out = []
    for s in symbols:
        pad = "    " if s.parent else ""
        out.append(f"{pad}{s.line}-{s.end}  {s.signature}")
    return "\n".join(out)
