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

from rlgate.core.task import Patch
from rlgate.variants.revert import Variant, _variant_id

MAX_VARIANTS = 300  # deterministic cap: take the first N in file/line/operator order

_REL_MAP = {"==": "!=", "!=": "==", "<=": "<", ">=": ">", "<": "<=", ">": ">="}
_REL_RE = re.compile(r"==|!=|<=|>=|<|>")
_LOGICAL_MAP = {"&&": "||", "||": "&&", "and": "or", "or": "and"}
_LOGICAL_RE = re.compile(r"&&|\|\||\band\b|\bor\b")
_BOOL_MAP = {"True": "False", "False": "True", "true": "false", "false": "true"}
_BOOL_RE = re.compile(r"\bTrue\b|\bFalse\b|\btrue\b|\bfalse\b")
_INT_RE = re.compile(r"\b\d+\b")
_RETURN_RE = re.compile(r"^(\s*)return\b(.*)$")

_ADJACENT_BAD = set("<>-=")  # avoid mutating <<, >>, ->, =>, <- digraphs


def _rel_ok(line: str, start: int, end: int, token: str) -> bool:
    if token not in ("<", ">"):
        return True
    before = line[start - 1] if start > 0 else ""
    after = line[end] if end < len(line) else ""
    return before not in _ADJACENT_BAD and after not in _ADJACENT_BAD


def _swaps(line: str, regex: re.Pattern, mapping: dict[str, str], op: str,
           rel_guard: bool = False) -> Iterator[tuple[str, str]]:
    for m in regex.finditer(line):
        tok = m.group(0)
        if rel_guard and not _rel_ok(line, m.start(), m.end(), tok):
            continue
        repl = mapping[tok]
        yield op, line[: m.start()] + repl + line[m.end():]


def _increment(line: str) -> Iterator[tuple[str, str]]:
    for m in _INT_RE.finditer(line):
        bumped = str(int(m.group(0)) + 1)
        yield "increment", line[: m.start()] + bumped + line[m.end():]


def _stub_return(line: str) -> Iterator[tuple[str, str]]:
    m = _RETURN_RE.match(line)
    if m and m.group(2).strip() not in ("", "0"):
        yield "stub_return", f"{m.group(1)}return 0"


def _mutations(line: str) -> Iterator[tuple[str, str]]:
    yield from _swaps(line, _REL_RE, _REL_MAP, "relational", rel_guard=True)
    yield from _swaps(line, _LOGICAL_RE, _LOGICAL_MAP, "logical")
    yield from _swaps(line, _BOOL_RE, _BOOL_MAP, "boolean")
    yield from _increment(line)
    yield from _stub_return(line)


def mutate_variants(gold: Patch) -> list[Variant]:
    """One variant per (added line, operator, match site), capped at ``MAX_VARIANTS``."""
    lines = gold.text.splitlines(keepends=True)
    variants: list[Variant] = []
    path: str | None = None
    hunk_idx = -1

    for i, raw in enumerate(lines):
        if raw.startswith("+++ "):
            path = raw[4:].strip().removeprefix("b/")
            hunk_idx = -1
            continue
        if raw.startswith("@@"):
            hunk_idx += 1
            continue
        if not raw.startswith("+") or raw.startswith("+++"):
            continue

        body = raw[1:]
        nl = ""
        if body.endswith("\n"):
            body, nl = body[:-1], "\n"

        for j, (op, mutated) in enumerate(_mutations(body)):
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
