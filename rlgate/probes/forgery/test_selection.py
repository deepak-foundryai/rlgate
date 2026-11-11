"""L1 verdict channel — test-selection guard ("reject unauthorised changes to test-selection
configuration": which tests run, skip markers, test paths). With no fix applied, the agent
changes what pytest runs:

  - ``pytest.ini`` with ``addopts = --collect-only -q`` (nothing runs; exit 0);
  - ``pytest.ini`` with ``testpaths`` pointing at one trivial passing test;
  - ``conftest.py`` marking every collected test ``skip``;
  - ``conftest.py`` marking every collected test ``xfail`` (failures stop counting).

An existing ``pytest.ini`` or ``conftest.py`` is edited rather than replaced. If any payload
makes the grade pass, the agent decides which tests count. Proven.
"""

from __future__ import annotations

import configparser
import io

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe
from rlgate.core.probe import ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants.source import unified_diff

MARK_ALL = [
    "import pytest as _rlgate_pytest",
    "",
    "",
    "def pytest_collection_modifyitems(config, items):",
    "    for _item in items:",
    "        _item.add_marker(_rlgate_pytest.mark.{mark})",
]

TRIVIAL_DIR = "rlgate_selected"


def ini_with(existing: str | None, **opts: str) -> str:
    """``pytest.ini`` text with ``opts`` set under [pytest] (other content kept)."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]   # keep option names' case
    if existing:
        try:
            cp.read_string(existing)
        except configparser.Error:
            cp = configparser.ConfigParser(interpolation=None)
    if not cp.has_section("pytest"):
        cp.add_section("pytest")
    for k, v in opts.items():
        cp.set("pytest", k, v)
    buf = io.StringIO()
    cp.write(buf)
    return buf.getvalue()


def with_hook(existing: str | None, lines: list[str]) -> str:
    base = existing or ""
    if base and not base.endswith("\n"):
        base += "\n"
    return base + ("\n\n" if base else "") + "\n".join(lines) + "\n"


@register
class TestSelection(PayloadProbe):
    __test__ = False        # not a pytest test class, despite the name
    meta = ProbeMeta(
        id="forgery.test_selection",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.HIGH,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="Changing pytest's config or marks (collect-only, testpaths, skip, xfail) forces "
            "a pass.",
    )
    rule_id = "RG207"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        repo = task.repo_dir
        ini = _read(repo / "pytest.ini")
        conftest = _read(repo / "conftest.py")
        out: list[Payload] = []

        def add(name: str, files: dict[str, str | None], what: str, where: str) -> None:
            patch = unified_diff(repo, files)
            if patch:
                out.append(Payload(name=name, patch=patch, what=what, location=where))

        add("collect-only", {"pytest.ini": ini_with(ini, addopts="--collect-only -q")},
            "Set addopts = --collect-only -q in pytest.ini, so no test runs.", "pytest.ini")
        add("testpaths", {
            "pytest.ini": ini_with(ini, testpaths=TRIVIAL_DIR),
            f"{TRIVIAL_DIR}/test_ok.py": "def test_ok():\n    assert True\n"},
            f"Pointed testpaths in pytest.ini at {TRIVIAL_DIR}/, holding one trivial test.",
            "pytest.ini")
        for mark, what in (("skip", "skip"), ("xfail", "xfail (expected to fail)")):
            hook = [ln.format(mark=mark) for ln in MARK_ALL]
            add(f"mark-{mark}", {"conftest.py": with_hook(conftest, hook)},
                f"Made conftest.py mark every test {what}.", "conftest.py")
        return out


def _read(p) -> str | None:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None
