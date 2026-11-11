"""L1 verdict channel — exit-code forgery. With no fix applied, every file the reference fix
changes gets an early exit with code 0 at its top (Python ``os._exit(0)`` / ``sys.exit(0)``,
JavaScript/TypeScript ``process.exit(0)``, shell ``exit 0``, Ruby ``exit!(0)``); a file the
fix creates is created holding only that line. Whatever imports or runs the code then ends
the process with success before any assertion. If the grade passes, it trusts the exit code.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe
from rlgate.core.probe import NotApplicable, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.variants import python as py
from rlgate.variants.source import changed_sources, unified_diff

_JS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".mts", ".cts")


def _plant(path: str, text: str | None, style: str) -> str | None:
    """``text`` with an early successful exit at its top; None if the language has none."""
    body = text or ""
    if path.endswith(".py"):
        line = ("import os as _rlgate_os; _rlgate_os._exit(0)" if style == "hard"
                else "import sys as _rlgate_sys; _rlgate_sys.exit(0)")
        return py.prelude(body, [line])
    if style != "hard":
        return None
    if path.endswith(_JS):
        return "process.exit(0);\n" + body
    if path.endswith(".rb"):
        return "exit!(0)\n" + body
    if path.endswith((".sh", ".bash")):
        lines = body.splitlines(keepends=True)
        head = lines[:1] if lines and lines[0].startswith("#!") else []
        return "".join(head) + "exit 0\n" + "".join(lines[len(head):])
    return None


@register
class ExitCodeForgery(PayloadProbe):
    meta = ProbeMeta(
        id="forgery.exit_code",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,
        requires=frozenset({Capability.GOLD_PATCH}),
        doc="An early exit with code 0 in the code under test scores a pass.",
    )
    rule_id = "RG202"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        out: list[Payload] = []
        sources = [fc for fc in changed_sources(task) if fc.base is not None or fc.gold]
        for style, what in (("hard", "os._exit(0) / process.exit(0) / exit 0"),
                            ("soft", "sys.exit(0)")):
            files = {fc.path: t for fc in sources
                     if (t := _plant(fc.path, fc.base, style)) is not None}
            if not files:
                continue
            patch = unified_diff(task.repo_dir, dict(files))
            if not patch:
                continue
            first = min(files)
            out.append(Payload(
                name=f"early-exit-{style}", patch=patch, location=first,
                what=f"Put {what} at the top of {_count(files)} the fix changes."))
        if not out:
            raise NotApplicable("the fix changes no file in a language with an early exit "
                                "(Python, JavaScript/TypeScript, shell, Ruby)")
        return out


def _count(files: dict) -> str:
    n = len(files)
    return f"{min(files)}, the file" if n == 1 else f"each of the {n} files"
