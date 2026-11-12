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


def mask(line: str, suffix: str, state: str | None) -> tuple[str, str | None]:
    """``line`` with string literals and comments blanked (same length), so operators are
    only found in code. ``state`` carries an open Python triple-quoted string or a C block
    comment from the line before; returns the state after this line."""
    out = list(line)
    i, n = 0, len(line)

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
