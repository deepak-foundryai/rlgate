"""Rubrics of rubric-graded tasks (reports, spreadsheets, documents), and the static rubric
checks: coverage, atomic criteria, observable criteria, no duplicates, weights that match
consequence.

A rubric is found next to the task: ``rubric.{json,yaml,yml,md,txt}`` in the task folder,
its ``tests/`` folder or a held-out test folder, or ``meta["rubric"]``. Accepted shapes:

  - JSON/YAML: a list of criteria, or ``{"criteria": [...]}`` / ``{"rubric": [...]}``; each
    criterion a string or ``{text|criterion|description, weight|points|score, id}``;
  - Markdown/text: one criterion per bullet or numbered line; a weight as ``[3]``,
    ``(3 points)``, ``(+2)``, ``(-5)`` or ``3 pts``.

The checks here are deterministic word-level heuristics, so every finding needs judgment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rlgate.core.task import Task

NAMES = ("rubric.json", "rubric.yaml", "rubric.yml", "rubric.md", "rubric.txt")
VAGUE = {"good", "well", "appropriate", "appropriately", "clear", "clearly", "quality",
         "professional", "professionally", "reasonable", "reasonably", "adequate",
         "adequately", "sufficient", "sufficiently", "properly", "nice", "thorough",
         "thoroughly", "comprehensive", "effective", "effectively", "relevant", "solid",
         "strong", "robust", "polished", "excellent", "acceptable"}
SERIOUS = {"correct", "correctly", "accurate", "accurately", "wrong", "error", "errors",
           "critical", "never", "safety", "safe", "legal", "compliance", "compliant",
           "calculation", "calculates", "total", "totals", "fraud", "harm", "risk"}
_STOP_WORDS = """a an the and or of to in on for with by from at as is are be been was were
it its this that these those must should shall will would can could may might include
includes including provide provides each every all any not no than then there their they
them his her our your you we which what who whom when where how why into onto over under
about between also only more most less least very such so if do does did done has have
had per via using use used make makes made"""
_STOP = set(_STOP_WORDS.split())
_REQ = re.compile(r"\b(must|should|shall|needs? to|include|includes|provide|report|list|"
                  r"calculate|compute|explain|compare|recommend|ensure|identify|summari[sz]e|"
                  r"estimate|propose|describe)\b", re.IGNORECASE)
_WEIGHT = re.compile(r"\[\s*([+-]?\d+(?:\.\d+)?)\s*\]|\(\s*([+-]?\d+(?:\.\d+)?)\s*"
                     r"(?:points?|pts?)?\s*\)|\b([+-]?\d+(?:\.\d+)?)\s*(?:points?|pts?)\b",
                     re.IGNORECASE)


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str
    weight: float | None = None


@dataclass(frozen=True)
class Rubric:
    path: str                      # where it was found (for findings)
    criteria: tuple[Criterion, ...]
    text: str                      # verbatim, for prompts

    @property
    def weighted(self) -> bool:
        return any(c.weight is not None for c in self.criteria)


def find(task: Task) -> Rubric | None:
    """The task's rubric, or None."""
    if task.meta.get("rubric"):
        raw = task.meta["rubric"]
        return _from_data(raw, "task metadata", json.dumps(raw, indent=1))
    dirs: list[Path] = []
    if task.origin:
        o = Path(task.origin)
        base = o if o.is_dir() else o.parent
        dirs += [base, base / "tests"]
    dirs += [Path(h) for h, _ in task.grader.inject]
    dirs += [r for r, _ in task.test_roots]
    for d in dict.fromkeys(dirs):
        for name in NAMES:
            p = d / name
            if p.is_file():
                r = load(p)
                if r is not None and r.criteria:
                    return r
    return None


def load(path: Path) -> Rubric | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    if path.suffix == ".json":
        try:
            return _from_data(json.loads(text), str(path), text)
        except ValueError:
            return None
    if path.suffix in (".yaml", ".yml"):
        import yaml
        try:
            return _from_data(yaml.safe_load(text), str(path), text)
        except yaml.YAMLError:
            return None
    return Rubric(str(path), tuple(_from_lines(text)), text)


def _from_data(data, where: str, text: str) -> Rubric | None:
    if isinstance(data, dict):
        data = data.get("criteria") or data.get("rubric") or data.get("items") or []
    if not isinstance(data, list):
        return None
    out = []
    for i, item in enumerate(data, 1):
        if isinstance(item, str):
            out.append(Criterion(f"C{i}", item.strip()))
        elif isinstance(item, dict):
            t = next((str(item[k]) for k in ("text", "criterion", "description", "name")
                      if item.get(k)), "")
            w = next((item[k] for k in ("weight", "points", "score") if k in item), None)
            try:
                w = float(w) if w is not None else None
            except (TypeError, ValueError):
                w = None
            if t.strip():
                out.append(Criterion(str(item.get("id") or f"C{i}"), t.strip(), w))
    return Rubric(where, tuple(out), text)


def _from_lines(text: str) -> list[Criterion]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]?\])\s+(.*\S)", line)
        if not m:
            continue
        body = m.group(1)
        w = _WEIGHT.search(body)
        weight = None
        if w:
            weight = float(next(g for g in w.groups() if g is not None))
            body = (body[:w.start()] + body[w.end():]).strip(" -:–")
        if body:
            out.append(Criterion(f"C{len(out) + 1}", body, weight))
    return out


# -- static checks --------------------------------------------------------------------------

def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if w not in _STOP}


def not_atomic(c: Criterion) -> str | None:
    t = c.text
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", t) if s.strip()]
    ands = len(re.findall(r"\band\b|;", t))
    if len(sentences) > 1:
        return f"it makes {len(sentences)} separate statements"
    if ands >= 2:
        return "it joins several checks with \"and\""
    if len(t.split()) > 40:
        return f"it is {len(t.split())} words long"
    return None


def not_observable(c: Criterion) -> str | None:
    found = sorted(words(c.text) & VAGUE)
    if not found:
        return None
    if re.search(r"\d|\"[^\"]+\"|'[^']+'", c.text):
        return None                    # anchored by a number or a quoted value
    return f"it relies on {', '.join(repr(w) for w in found)} with nothing measurable"


def duplicates(r: Rubric) -> list[tuple[Criterion, Criterion]]:
    out = []
    ws = [(c, words(c.text)) for c in r.criteria]
    for i, (a, wa) in enumerate(ws):
        for b, wb in ws[i + 1:]:
            if wa and wb and len(wa & wb) / len(wa | wb) >= 0.75:
                out.append((a, b))
    return out


def weight_problems(r: Rubric) -> list[str]:
    out = []
    ws = [c.weight for c in r.criteria if c.weight is not None]
    if len(r.criteria) >= 4:
        serious = [c for c in r.criteria if words(c.text) & SERIOUS]
        if not r.weighted:
            if serious:
                out.append(f"no criterion has a weight, yet {serious[0].id} (\""
                           f"{serious[0].text[:80]}\") guards against a serious mistake")
        elif len(set(ws)) == 1 and serious:
            out.append(f"every criterion weighs {ws[0]:g}, yet {serious[0].id} (\""
                       f"{serious[0].text[:80]}\") guards against a serious mistake")
    if len(r.criteria) >= 5 and not any(w < 0 for w in ws) and not any(
            re.search(r"\b(penalt|deduct|subtract|minus)", c.text, re.IGNORECASE)
            for c in r.criteria):
        out.append("there are no penalty criteria for serious errors")
    return out


def requirements(statement: str) -> list[str]:
    """Sentences of the statement that ask for something."""
    parts = re.split(r"(?<=[.!?])\s+|\n\s*[-*•]\s+|\n{2,}", statement)
    return [p.strip() for p in parts if _REQ.search(p) and len(words(p)) >= 2]


def uncovered(statement: str, r: Rubric) -> list[str]:
    """Requirements no criterion shares its key words with."""
    crit = [words(c.text) for c in r.criteria]
    out = []
    for req in requirements(statement):
        w = words(req)
        need = 2 if len(w) > 3 else 1
        if not any(len(w & cw) >= need for cw in crit):
            out.append(req)
    return out
