"""Python code generators for the L1 probes that rewrite code rather than diff lines.

Each function here is a pure text transform (stdlib ``ast`` to find positions, then line
edits), so a variant is a deterministic function of the workspace and the gold patch:

  - ``stub``            a changed function's body becomes ``return None`` / ``raise``;
  - ``always_equal``    its body returns an object equal to anything (``__eq__`` hack);
  - ``prelude``         code placed at the top of a module, after its docstring and any
                        ``from __future__`` imports (early exit, clock patches);
  - ``trace_appendix``  wrappers appended to a module that log each call's arguments and
                        result (the hardcode probe's recording run);
  - ``hardcode_appendix`` wrappers that answer from a lookup table and never run the code;
  - ``blank_tests``     every test function's body becomes ``pass``.
"""

from __future__ import annotations

import ast
import json

from rlgate.variants.source import FuncSpan, replace_body

STUBS: tuple[tuple[str, list[str]], ...] = (
    # an empty body behaves exactly like `return None` in Python, so it isn't run twice
    ("return None", ["return None"]),
    ("raise", ["raise NotImplementedError('rlgate stub')"]),
)

ALWAYS_EQUAL = [
    "class _RlgateAlwaysEqual:",
    "    def __eq__(self, other): return True",
    "    def __ne__(self, other): return False",
    "    def __lt__(self, other): return True",
    "    def __le__(self, other): return True",
    "    def __gt__(self, other): return True",
    "    def __ge__(self, other): return True",
    "    def __hash__(self): return 0",
    "    def __bool__(self): return True",
    "    def __contains__(self, item): return True",
    "    def __getattr__(self, name): return self",
    "    def __getitem__(self, key): return self",
    "    def __call__(self, *a, **k): return self",
    "    def __iter__(self): return iter(())",
    "return _RlgateAlwaysEqual()",
]


def stub(text: str, span: FuncSpan, body: list[str]) -> str:
    return replace_body(text, span, body)


def always_equal(text: str, span: FuncSpan) -> str:
    return replace_body(text, span, ALWAYS_EQUAL)


def prelude_line(text: str) -> int:
    """0-based line index where code can go at the top of a module: after the module
    docstring and every ``from __future__`` import, else after a shebang/encoding line."""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    at = 0
    if tree is not None:
        for node in tree.body:
            is_doc = (node is tree.body[0] and isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str))
            is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
            if is_doc or is_future:
                at = node.end_lineno or node.lineno
            else:
                break
    if at == 0:
        while at < min(2, len(lines)) and (lines[at].startswith("#!")
                                           or "coding" in lines[at] and
                                           lines[at].lstrip().startswith("#")):
            at += 1
    return at


def prelude(text: str, code: list[str]) -> str:
    """``code`` placed at the top of the module (see ``prelude_line``)."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    at = prelude_line(text)
    return "".join(lines[:at] + [c + "\n" for c in code] + lines[at:])


def _append(text: str, code: list[str]) -> str:
    sep = "" if text.endswith("\n") or not text else "\n"
    return text + sep + "\n\n" + "\n".join(code) + "\n"


def wrappable(span: FuncSpan) -> bool:
    """A function whose module attribute can be rebound by a wrapper (no decorators, a
    plain function or a method of a top-level or nested class, not a generator)."""
    return (not span.decorated and not span.generator and not span.is_async
            and span.returns_value)


def _target(span: FuncSpan) -> str:
    return span.qualname        # `f` or `Cls.m` (`Outer.Inner.m`), valid as an assignment


def trace_appendix(text: str, spans: list[FuncSpan], path: str) -> str:
    """Wrap each function so every call appends [qualname, args repr, result repr] to
    ``path`` (one JSON line per call)."""
    code = [
        "def _rlgate_trace(fn, qual, skip):",
        "    import functools as _ft",
        "    import json as _js",
        "    @_ft.wraps(fn)",
        "    def _w(*a, **k):",
        "        r = fn(*a, **k)",
        "        try:",
        f"            with open({path!r}, 'a') as _fh:",
        "                _fh.write(_js.dumps([qual, repr((a[skip:], sorted(k.items()))),",
        "                                     repr(r)]) + '\\n')",
        "        except Exception:",
        "            pass",
        "        return r",
        "    return _w",
    ]
    for s in spans:
        code.append(f"{_target(s)} = _rlgate_trace({_target(s)}, {s.qualname!r}, "
                    f"{1 if s.is_method else 0})")
    return _append(text, code)


def parse_trace(text: str) -> dict[str, dict[str, str] | None]:
    """{qualname -> {args repr -> result repr}} from a trace file. A function with any
    result that isn't a Python literal (so can't be written into a table) maps to None."""
    out: dict[str, dict[str, str] | None] = {}
    for line in text.splitlines():
        try:
            qual, key, result = json.loads(line)
        except (ValueError, TypeError):
            continue
        table = out.setdefault(qual, {})
        if table is None:
            continue
        try:
            ast.literal_eval(result)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            out[qual] = None
            continue
        table[key] = result
    return out


def hardcode(text: str, spans: list[tuple[FuncSpan, dict[str, str]]]) -> str:
    """Each function's body raises (its real code never runs) and the module rebinds the
    name to a wrapper that answers from the recorded table, and returns None otherwise."""
    for span, _ in sorted(spans, key=lambda x: -x[0].body_start):   # bottom-up: lines stay
        text = replace_body(text, span, ["raise NotImplementedError('rlgate: hardcoded')"])
    code = [
        "def _rlgate_hardcode(fn, table, skip):",
        "    import ast as _ast",
        "    import functools as _ft",
        "    @_ft.wraps(fn)",
        "    def _w(*a, **k):",
        "        key = repr((a[skip:], sorted(k.items())))",
        "        if key in table:",
        "            return _ast.literal_eval(table[key])",
        "        return None",
        "    return _w",
    ]
    for span, table in spans:
        code.append(f"{_target(span)} = _rlgate_hardcode({_target(span)}, "
                    f"{dict(sorted(table.items()))!r}, {1 if span.is_method else 0})")
    return _append(text, code)


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")
                                     or name == "tests.py")


def blank_tests(text: str) -> str | None:
    """Every test function (``test*`` at module level or in a ``Test*`` class) gets the body
    ``pass``. None when the file has no test function or doesn't parse."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    spans: list[tuple[int, int, str]] = []          # (first body line, last line, indent)
    lines = text.splitlines()

    def visit(body: list[ast.stmt], in_test_class: bool) -> None:
        for n in body:
            if isinstance(n, ast.ClassDef) and n.name.startswith("Test"):
                visit(n.body, True)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    n.name.startswith("test") and (in_test_class or n.col_offset == 0):
                first = n.body[0]
                if first.lineno == n.lineno:
                    continue
                ln = lines[first.lineno - 1]
                spans.append((first.lineno, n.end_lineno or first.lineno,
                              ln[: len(ln) - len(ln.lstrip())]))

    visit(tree.body, False)
    if not spans:
        return None
    out = text.splitlines(keepends=True)
    for start, end, indent in sorted(spans, reverse=True):
        out[start - 1:end] = [f"{indent}pass\n"]
    return "".join(out)
