"""Deterministic MUTATION operators (ARCHITECTURE §2a, PLAN §3.4).

Where ``revert`` drops a whole gold hunk (non-equivalent by construction → CONFIRMED), the
mutation operators make a *single-token* or single-line change to one line the gold patch
adds: flip a comparison, swap a boolean, bump an integer, or stub a ``return``. The variant is
the full gold patch with that one added line rewritten — still a valid diff against the base
(line counts are unchanged, so the hunk headers stay valid).

A surviving mutant (builds, no gold-passing test regresses) is *ambiguous*: it may be an
equivalent mutant (benign) or a real coverage gap. The deterministic core cannot tell these
apart, so the mutation probe reports survivors at REVIEW, never CONFIRMED.

Generation is a pure function of the patch bytes: no RNG, no search, versioned operator set.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePosixPath

from rlgate.core.task import Patch
from rlgate.variants._code import mask
from rlgate.variants.revert import Variant, _variant_id, _vcs_metadata, generated

MAX_VARIANTS = 300  # deterministic cap: take the first N in file/line/operator order

_REL_MAP = {"==": "!=", "!=": "==", "<=": "<", ">=": ">", "<": "<=", ">": ">="}
_REL_RE = re.compile(r"==|!=|<=|>=|<|>")
_LOGICAL_MAP = {"&&": "||", "||": "&&", "and": "or", "or": "and"}
_LOGICAL_RE = re.compile(r"&&|\|\||\band\b|\bor\b")
_BOOL_MAP = {"True": "False", "False": "True", "true": "false", "false": "true"}
_BOOL_RE = re.compile(r"\bTrue\b|\bFalse\b|\btrue\b|\bfalse\b")
_INT_RE = re.compile(r"\b\d+\b")
_RETURN_RE = re.compile(r"^(\s*)return\b(.*)$")
# a branch condition: Python `if/elif cond:` or C-like `if (cond) {` / `} else if (cond) {`
_PY_IF_RE = re.compile(r"^(?P<lead>\s*(?:el)?if\s+)(?P<cond>.+?)(?P<tail>:\s*(?:#.*)?)$")
_C_IF_RE = re.compile(r"^(?P<lead>\s*(?:\}\s*)?(?:else\s+)?if\s*\()(?P<cond>.+)"
                      r"(?P<tail>\)\s*\{?\s*(?://.*)?)$")

_ADJACENT_BAD = set("<>-=")  # avoid mutating <<, >>, ->, =>, <- digraphs
# configuration flags whose flip no test is expected to see (dataclass/pydantic options)
_FLAG_RE = re.compile(r"\b(?:strict|frozen|slots|kw_only|repr|eq|order|unsafe_hash|init|"
                      r"exist_ok)\s*=\s*$")
def _rel_ok(line: str, start: int, end: int, token: str) -> bool:
    if token not in ("<", ">"):
        return True
    before = line[start - 1] if start > 0 else ""
    after = line[end] if end < len(line) else ""
    return before not in _ADJACENT_BAD and after not in _ADJACENT_BAD


def _swaps(line: str, code: str, regex: re.Pattern, mapping: dict[str, str], op: str,
           rel_guard: bool = False) -> Iterator[tuple[str, str]]:
    for m in regex.finditer(code):
        tok = m.group(0)
        if rel_guard and not _rel_ok(line, m.start(), m.end(), tok):
            continue
        if op == "boolean" and _FLAG_RE.search(code[: m.start()]):
            continue
        repl = mapping[tok]
        yield op, line[: m.start()] + repl + line[m.end():]


def _increment(line: str, code: str) -> Iterator[tuple[str, str]]:
    for m in _INT_RE.finditer(code):
        bumped = str(int(m.group(0)) + 1)
        yield "increment", line[: m.start()] + bumped + line[m.end():]


def _decrement(line: str, code: str) -> Iterator[tuple[str, str]]:
    for m in _INT_RE.finditer(code):
        n = int(m.group(0))
        if n > 0:
            yield "decrement", line[: m.start()] + str(n - 1) + line[m.end():]


def _parts(line: str, m: re.Match) -> tuple[str, str, str]:
    """The lead/cond/tail groups of a match on the masked line, cut from the real one."""
    return (line[: m.end("lead")], line[m.start("cond"): m.end("cond")],
            line[m.start("tail"):])


def _branches(line: str, code: str) -> Iterator[tuple[str, str]]:
    """Negate a branch condition, and drop the branch (its condition becomes false)."""
    py = _PY_IF_RE.match(code)
    if py and not line.lstrip().startswith(("if __name__", "elif __name__")):
        lead, cond, tail = _parts(line, py)
        yield "negate_condition", f"{lead}not ({cond}){tail}"
        yield "drop_branch", f"{lead}False{tail}"
        return
    c = _C_IF_RE.match(code)
    if c:
        lead, cond, tail = _parts(line, c)
        yield "negate_condition", f"{lead}!({cond}){tail}"
        yield "drop_branch", f"{lead}false{tail}"


def _stub_return(line: str, code: str) -> Iterator[tuple[str, str]]:
    if not _RETURN_RE.match(code):
        return
    m = _RETURN_RE.match(line)
    if m and m.group(2).strip() not in ("", "0"):
        yield "stub_return", f"{m.group(1)}return 0"


def _mutations(line: str, code: str | None = None) -> Iterator[tuple[str, str]]:
    """Mutants of ``line``; operators are looked for in ``code`` (the line with its
    strings and comments blanked), so a docstring's "or" or a comment's 2 stay put."""
    code = line if code is None else code
    if not code.strip():
        return
    yield from _swaps(line, code, _REL_RE, _REL_MAP, "relational", rel_guard=True)
    yield from _swaps(line, code, _LOGICAL_RE, _LOGICAL_MAP, "logical")
    yield from _swaps(line, code, _BOOL_RE, _BOOL_MAP, "boolean")
    yield from _increment(line, code)
    yield from _decrement(line, code)
    yield from _branches(line, code)
    yield from _stub_return(line, code)


def mutate_variants(gold: Patch) -> list[Variant]:
    """One variant per (added line, operator, match site), capped at ``MAX_VARIANTS``."""
    lines = gold.text.splitlines(keepends=True)
    variants: list[Variant] = []
    path: str | None = None
    hunk_idx = -1

    suffix, skip, state = "", False, None
    for i, raw in enumerate(lines):
        if raw.startswith("+++ "):
            # `+++ b/path<TAB>2026-08-30 01:19:07`: diff -u adds a timestamp after a tab
            path = raw[4:].split("\t", 1)[0].strip().removeprefix("b/")
            hunk_idx = -1
            # build output, docs and VCS metadata (.git/logs) aren't the fix's behaviour
            skip = path == "/dev/null" or generated(path) or _vcs_metadata(path)
            suffix = PurePosixPath(path).suffix.lower()
            state = None
            continue
        if raw.startswith("@@"):
            hunk_idx += 1
            state = None      # a hunk starts mid-file: we can't know what's open before it
            continue
        if skip or raw.startswith(("-", "\\", "+++")):
            continue

        body = raw[1:]
        nl = ""
        if body.endswith("\n"):
            body, nl = body[:-1], "\n"
        code, state = mask(body, suffix, state)
        if not raw.startswith("+"):
            continue          # a context line: only read for an open docstring

        for j, (op, mutated) in enumerate(_mutations(body, code)):
            if mutated == body:
                continue
            new_lines = lines.copy()
            new_lines[i] = "+" + mutated + nl
            origin = f"{path}#hunk{max(hunk_idx, 0)}:{op}"
            variants.append(Variant(
                id=_variant_id(gold.sha256, f"{origin}@L{i}#{j}", op),  # j disambiguates sites
                operator=op,
                patch_text="".join(new_lines),
                origin=origin,
            ))
            if len(variants) >= MAX_VARIANTS:
                return variants
    return variants
