"""§3.1 — the CONFIRMING counterpart to unrestored_test_infra, for pytest tasks. Inject a
root conftest.py whose makereport hook forces every test to "passed", apply it on the
feature-less base (where honest work fails), and run the grader. If the grader now scores
pass, the verdict channel is forgeable — proven by execution, not inferred. CONFIRMED.

This is a PATTERN probe: a pytest-specific payload, run live. A root conftest.py that already
exists gets the hook appended (as an agent would edit it). If the task doesn't use pytest, the
payload simply doesn't take and we report nothing.
"""

from __future__ import annotations

from pathlib import Path

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe
from rlgate.core.probe import ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants.source import unified_diff

_HOOK = [
    "import pytest",
    "",
    "",
    "@pytest.hookimpl(hookwrapper=True)",
    "def pytest_runtest_makereport(item, call):",
    "    outcome = yield",
    "    rep = outcome.get_result()",
    "    rep.outcome = 'passed'",
    "    rep.longrepr = None",
]


def _conftest_patch(repo: Path) -> str:
    """A root conftest.py with the hook: created, or appended to the one already there."""
    existing = repo / "conftest.py"
    try:
        base = existing.read_text(encoding="utf-8") if existing.is_file() else ""
    except (OSError, UnicodeDecodeError):
        base = ""
    if base and not base.endswith("\n"):
        base += "\n"
    text = base + ("\n\n" if base else "") + "\n".join(_HOOK) + "\n"
    return unified_diff(repo, {"conftest.py": text})


@register
class ConftestInjection(PayloadProbe):
    meta = ProbeMeta(
        id="forgery.conftest_injection",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.HIGH,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="A candidate-injected root conftest.py forces pytest to report every test passed.",
    )
    rule_id = "RG201"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        had = (task.repo_dir / "conftest.py").is_file()
        return [Payload(
            name="root-conftest-makereport",
            patch=_conftest_patch(task.repo_dir),
            what=("Added a makereport hook that marks every test passed to the existing "
                  "root conftest.py." if had else
                  "Added a root conftest.py whose makereport hook marks every test passed."),
            location="conftest.py",
        )]
