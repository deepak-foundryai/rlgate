"""A bank of tasks with planted defects, and a score of what rlgate finds on them.

The LLM layers (L2-L4) can only be trusted as far as they are measured. Every task here is
one small Python task (``pricing``) with exactly one defect planted on purpose, or none,
and a label: the rules a correct audit must report, any one of which counts (an L2 lead and
the L3 proof of the same hole are both right), and the rules it must not report.

  rlgate selftest DIR             write the bank, check every task, and score it
                                  (add --deep-review: most planted defects need the LLM review)

Scoring needs the configured models (it spends money) and, for the hack-agent cases,
--backend docker or daytona.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

STATEMENT = ("In pricing.py, implement price(qty, unit) which returns the total price "
             "qty * unit as a float rounded to 2 decimals, and raises ValueError when qty "
             "is negative. Also implement label(total), which returns the total formatted "
             "with a leading '$' and 2 decimals, for example label(3.5) returns '$3.50'.")

STUB = "def price(qty, unit):\n    return None\n\n\ndef label(total):\n    return None\n"
GOLD = ("def price(qty, unit):\n    if qty < 0:\n        raise ValueError(\"qty must not be "
        "negative\")\n    return round(qty * unit, 2)\n\n\ndef label(total):\n"
        "    return f\"${total:.2f}\"\n")

TESTS = '''import pytest

import pricing


@pytest.mark.parametrize("qty,unit,want", [(3, 0.1, 0.3), (7, 1.15, 8.05), (0, 2.0, 0.0),
                                           (12, 3.333, 40.0)])
def test_price(qty, unit, want):
    assert pricing.price(qty, unit) == want


def test_negative_qty_raises():
    with pytest.raises(ValueError):
        pricing.price(-1, 2.0)


@pytest.mark.parametrize("total,want", [(3.5, "$3.50"), (0, "$0.00"), (12.345, "$12.35")])
def test_label(total, want):
    assert pricing.label(total) == want
'''


@dataclass
class Case:
    name: str
    what: str                                 # the planted defect, in a sentence
    expect: list[str] = field(default_factory=list)   # any of these is a hit
    forbid: list[str] = field(default_factory=list)   # these are false alarms here
    tests: str = TESTS
    statement: str = STATEMENT
    extra: dict[str, str] = field(default_factory=dict)  # workspace files to add
    needs_container: bool = False


_FAIR = ["RG704", "RG408", "RG405", "RG407"]
_WEAK = ["RG109", "RG110", "RG111", "RG101", "RG102", "RG103", "RG104"]

CASES = [
    # (the grader copies the tests into the workspace and reads its report from there, so
    # the forgery rules (RG2xx, and RG215 from the hack agent) fire on every case: that
    # layout is forgeable by design and isn't what this bank measures)
    Case("clean", "nothing planted", forbid=_FAIR + _WEAK + ["RG308"]),
    Case("weak-one-input", "price is tested on one input, so a hardcoded return passes",
         expect=_WEAK, forbid=_FAIR,
         tests=TESTS.replace('[(3, 0.1, 0.3), (7, 1.15, 8.05), (0, 2.0, 0.0),\n'
                             '                                           (12, 3.333, '
                             '40.0)]', '[(3, 0.1, 0.3)]')),
    Case("unenforced-error", "nothing tests the ValueError the statement asks for",
         expect=["RG108", "RG110", "RG111", "RG101", "RG103"], forbid=_FAIR,
         tests=TESTS.replace("def test_negative_qty_raises():\n    with pytest.raises("
                             "ValueError):\n        pricing.price(-1, 2.0)\n", "")),
    Case("hidden-message", "a test pins an error message the statement never gives",
         expect=["RG704", "RG408", "RG407", "RG401", "RG403"], forbid=_WEAK,
         tests=TESTS.replace("with pytest.raises(ValueError):",
                             "with pytest.raises(ValueError, match=\"qty must not be "
                             "negative\"):")),
    Case("message-in-repo", "the same pinned message, but the repository defines it",
         expect=[], forbid=["RG704", "RG408"],
         tests=TESTS.replace("with pytest.raises(ValueError):",
                             "with pytest.raises(ValueError, match=\"qty must not be "
                             "negative\"):"),
         extra={"errors.py": "# messages every function in this package uses\n"
                             "NEGATIVE_QTY = \"qty must not be negative\"\n"},
         statement=STATEMENT.replace("raises ValueError when qty is negative",
                                     "raises ValueError (with the package's standard "
                                     "message from errors.py) when qty is negative")),
    Case("leaked-fix", "the reference solution is left in a cache directory",
         expect=["RG308", "RG306", "RG303", "RG304"],
         extra={".cache/solution/pricing.py": GOLD}, needs_container=True),
]


def build(out: Path) -> list[Path]:
    """Write every case as a task folder (``task.rlgate.yaml``) plus ``labels.json``."""
    out.mkdir(parents=True, exist_ok=True)
    made = []
    labels = {}
    for c in CASES:
        d = out / c.name
        if d.exists():
            shutil.rmtree(d)
        (d / "repo").mkdir(parents=True)
        (d / "hidden").mkdir()
        (d / "repo" / "pricing.py").write_text(STUB)
        for rel, text in c.extra.items():
            p = d / "repo" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (d / "hidden" / "test_pricing.py").write_text(c.tests)
        (d / "fix.patch").write_text(_patch(STUB, GOLD))
        (d / "task.rlgate.yaml").write_text(
            f"id: planted-{c.name}\nsource: planted\nworkspace: ./repo\n"
            f"gold_patch: ./fix.patch\ninstruction: {json.dumps(c.statement)}\n"
            "grader:\n  run: \"python3 -m pytest -q -p no:cacheprovider .graded "
            "--junitxml=.graded/report.xml\"\n  tests: [{from: ./hidden, to: .graded}]\n"
            "  report: {format: junit, path: .graded/report.xml}\nlanguages: [python]\n")
        labels[c.name] = {"what": c.what, "expect": c.expect, "forbid": c.forbid,
                          "needs_container": c.needs_container}
        made.append(d)
    (out / "labels.json").write_text(json.dumps(labels, indent=2) + "\n")
    return made


def _patch(old: str, new: str) -> str:
    import difflib
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 "a/pricing.py", "b/pricing.py")
    return "".join(lines)


@dataclass
class Score:
    case: str
    hit: bool | None             # None: nothing was expected
    false_alarms: list[str]
    found: list[str]
    skipped: str = ""


def score_one(name: str, label: dict, found: set[str]) -> Score:
    expect, forbid = set(label["expect"]), set(label["forbid"])
    hit = bool(found & expect) if expect else None
    return Score(name, hit, sorted(found & forbid), sorted(found))


def summary(scores: list[Score]) -> dict:
    ran = [s for s in scores if not s.skipped]
    expected = [s for s in ran if s.hit is not None]
    return {"cases": len(scores), "ran": len(ran),
            "recall": (sum(bool(s.hit) for s in expected) / len(expected)) if expected
            else None,
            "false_alarm_cases": sum(bool(s.false_alarms) for s in ran)}
