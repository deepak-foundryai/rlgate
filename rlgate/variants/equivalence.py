"""Equivalence (free-variation) variant generation — the inverse of mutation testing.

A mutation is wrong-by-construction and *should* be killed. An equivalence transform changes
something the spec leaves free, so a correct solution is unaffected and the grade *must not*
change; if it does, a test is over-fit to an incidental.

To stay safe (never break the solution itself) we only vary string literals that are clearly
message-like — arguments to a print/log/raise/panic/console call — AND whose content does not
appear in the instruction (so it isn't a required output) AND contain no format specifiers or
braces (so rewriting can't cause a runtime formatting error). That is conservative on purpose:
few candidates, but a broken grade is a real over-fit, provable by execution.
"""

from __future__ import annotations

import re

from rlgate.core.task import Patch
from rlgate.variants.revert import Variant, _variant_id

MAX_VARIANTS = 100
SENTINEL = "rlgate_equiv_sentinel"

_MSG_CTX = re.compile(
    r"\b(print|printf|println|puts|echo|log|logger|logging|warn|warning|error|raise|panic|"
    r"throw|fmt\.(?:Print|Errorf|Sprintf)\w*|console\.(?:log|warn|error|info))\b",
    re.IGNORECASE,
)
# A plain string literal: no quotes/backslash/format-specifier/brace chars inside, len >= 3.
_LITERAL = re.compile(r"(['\"])([^'\"\\%{}]{3,})\1")


def equivalence_variants(gold: Patch, instruction: str | None) -> list[Variant]:
    if not instruction:
        return []  # without the spec we can't tell an incidental from a requirement
    instr = instruction.lower()
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
        if not _MSG_CTX.search(body):
            continue

        for j, m in enumerate(_LITERAL.finditer(body)):
            content = m.group(2)
            if content.lower() in instr or SENTINEL in content:
                continue  # the string is required by the spec, or already sentinel
            new_body = body[: m.start(2)] + SENTINEL + body[m.end(2):]
            new_lines = lines.copy()
            new_lines[i] = "+" + new_body
            origin = f"{path}#hunk{max(hunk_idx, 0)}:message-string"
            variants.append(Variant(
                id=_variant_id(gold.sha256, f"{origin}@L{i}#{j}", "equivalence"),
                operator="equivalence",
                patch_text="".join(new_lines),
                origin=origin,
            ))
            if len(variants) >= MAX_VARIANTS:
                return variants
    return variants
