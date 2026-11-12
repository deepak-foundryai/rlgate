"""Telling code from comments and string literals, line by line, for the variant
generators: a mutant should change code, and a hunk that only touches comments or
docstrings changes nothing a program does."""

from __future__ import annotations

HASH = {".py", ".pyi", ".sh", ".bash", ".rb", ".pl", ".r", ".yaml", ".yml", ".toml", ".cfg",
        ".ini", ".nix", ".jl", ".ex", ".exs", ".tf", ".mk", ".cmake", ".nim", ""}
SLASH = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".cs", ".java", ".kt", ".kts",
         ".scala", ".go", ".rs", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".swift",
         ".dart", ".php", ".zig", ".sol", ".v"}

QUOTE_STR = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".php", ".dart", ".swift"}
# `--` line comments; block comments that open/close with their own tokens
DASH = {".lean", ".hs", ".lhs", ".elm", ".agda", ".idr", ".sql", ".lua", ".ada", ".adb", ".ads"}
BLOCK = {".lean": ("/-", "-/"), ".hs": ("{-", "-}"), ".lhs": ("{-", "-}"), ".elm": ("{-", "-}"),
         ".agda": ("{-", "-}"), ".idr": ("{-", "-}"), ".lua": ("--[[", "]]"),
         ".ml": ("(*", "*)"), ".mli": ("(*", "*)"), ".fs": ("(*", "*)"), ".v": ("(*", "*)"),
         ".sql": ("/*", "*/")}
# markup and templates: only what sits between template delimiters is code
MARKUP = {".html", ".htm", ".xml", ".xhtml", ".svg", ".jinja", ".jinja2", ".j2", ".njk",
          ".hbs", ".mustache", ".ejs", ".erb", ".liquid", ".twig", ".md", ".markdown", ".rst",
          ".adoc"}
_DELIMS = (("{{", "}}"), ("{%", "%}"), ("<%", "%>"), ("${", "}"))


def mask(line: str, suffix: str, state: str | None) -> tuple[str, str | None]:
    """``line`` with string literals and comments blanked (same length), so operators are
    only found in code. ``state`` carries an open Python triple-quoted string or a C block
    comment from the line before; returns the state after this line."""
    if suffix in MARKUP:
        return _mask_markup(line, state)
    out = list(line)
    i, n = 0, len(line)
    block = BLOCK.get(suffix)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            out[k] = " "
    while i < n:
        if state is not None:                      # inside a docstring / block comment
            end = line.find(state, i)
            if end < 0:
                blank(i, n)
                return "".join(out), state
            blank(i, end + len(state))
            i, state = end + len(state), None
            continue
        c = line[i]
        if block and line.startswith(block[0], i):
            state = block[1]
            blank(i, i + len(block[0]))
            i += len(block[0])
            continue
        if suffix in DASH and line.startswith("--", i):
            blank(i, n)
            break
        if suffix in HASH and c == "#":
            blank(i, n)
            break
        if suffix in SLASH and line.startswith("//", i):
            blank(i, n)
            break
        if suffix in SLASH and line.startswith("/*", i):
            state = "*/"
            blank(i, i + 2)
            i += 2
            continue
        if suffix in (".py", ".pyi") and line.startswith(('"""', "\'\'\'"), i):
            state = line[i:i + 3]
            blank(i, i + 3)
            i += 3
            continue
        if c in "\"'`":
            if c == "'" and suffix in SLASH and suffix not in QUOTE_STR:
                close = line.find("'", i + 1)        # a char literal ('c', '\\n'), not
                if not 0 < close - i <= 3 and not (  # a lifetime ('a) or a Lean prime
                        close - i == 4 and line[i + 1] == "\\"):
                    i += 1
                    continue
            j = i + 1
            while j < n and line[j] != c:
                j += 2 if line[j] == "\\" else 1
            if j < n:                              # a closed literal (not a Rust lifetime)
                blank(i, j + 1)
                i = j + 1
                continue
        i += 1
    return "".join(out), state


def _mask_markup(line: str, state: str | None) -> tuple[str, str | None]:
    """Markup and prose: everything blanked but template expressions (``{{ a > b }}``,
    ``{% if x and y %}``); ``state`` is the closing delimiter of an open expression."""
    out = [" "] * len(line)
    i, n = 0, len(line)
    while i < n:
        if state is None:
            hit = min(((line.find(o, i), o, c) for o, c in _DELIMS if line.find(o, i) >= 0),
                      default=None)
            if hit is None:
                break
            i, state = hit[0] + len(hit[1]), hit[2]
            continue
        end = line.find(state, i)
        stop = n if end < 0 else end
        out[i:stop] = line[i:stop]
        if end < 0:
            return "".join(out), state
        i, state = end + len(state), None
    return "".join(out), None
