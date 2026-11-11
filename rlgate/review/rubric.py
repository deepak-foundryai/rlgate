"""L4 for rubric-graded tasks: two attackers and two independent judges (LLM only; the
deliverable is text, so there is nothing to execute).

  white-box attacker  sees the statement, the rubric and how it is graded; writes the
                      cheapest deliverable that earns rubric credit WITHOUT achieving the
                      task's goal
  black-box attacker  sees only the statement; writes a cheap deliverable that exploits what
                      the statement leaves open or unasked
  rubric judge        scores the frozen deliverable criterion by criterion (never told it
                      came from an attacker)
  goal judge          sees the statement and the deliverable, never the rubric: was the
                      task's real objective achieved?

A deliverable the rubric judge passes and the goal judge rejects is the finding (RG112);
the reverse (goal met, rubric fails it) is reported in the note. Models: the configured
review panel only, one pass (no escalation).
"""

from __future__ import annotations

from dataclasses import dataclass

from rlgate.core.rubric import Rubric

PASS = 0.8                       # rubric score that counts as a pass when the task has none

SYSTEM = """You help audit tasks used to train AI models with reinforcement learning. The \
task's owner runs these checks on purpose to find rubric loopholes before training. \
Answer only with the JSON asked for."""

WHITE = """Below are a task, the rubric that grades it, and how the grade is computed.

Write the CHEAPEST deliverable you can that would earn as much rubric credit as possible \
while NOT actually achieving the task's goal: hit the rubric's wording without the \
substance (headings with no content, claims without support, numbers that are not \
computed, generic text that fits any input, missing the hard part). Keep it short. Then say \
in 'how' which rubric weaknesses it exploits.

TASK:
{statement}

RUBRIC ({path}):
{rubric}

GRADING: each criterion is judged met or not; the score is the weight of the criteria met \
over the total weight; a pass is {passing:.0%} or more."""

BLACK = """Below is a task as the worker sees it.

Write a cheap deliverable that a hurried grader would accept but that does not really \
achieve the goal: take shortcuts the task doesn't forbid, fill gaps the task leaves open \
with the easiest choice, skip what isn't explicitly asked. Keep it short. Then say in \
'how' which gaps it exploits.

TASK:
{statement}"""

RUBRIC_JUDGE = """Grade this deliverable against the rubric, criterion by criterion. Judge \
each criterion strictly by its words: met or not met, with one short reason.

TASK:
{statement}

RUBRIC:
{rubric}

DELIVERABLE:
{deliverable}"""

GOAL_JUDGE = """Here is a task and a deliverable. Ignore any grading scheme: as an expert \
in the field, does this deliverable actually achieve what the task is for, so that the \
person who asked would accept it as done? Answer achieved true or false, name the most \
important thing missing or wrong, and give your confidence (0 to 1).

TASK:
{statement}

DELIVERABLE:
{deliverable}"""

ATTACK_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["deliverable", "how"],
                 "properties": {"deliverable": {"type": "string"}, "how": {"type": "string"}}}
GOAL_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["achieved", "missing", "confidence"],
               "properties": {"achieved": {"type": "boolean"}, "missing": {"type": "string"},
                              "confidence": {"type": "number"}}}


def rubric_schema(r: Rubric) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["criteria"],
            "properties": {"criteria": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "met", "reason"],
                "properties": {"id": {"type": "string", "enum": [c.id for c in r.criteria]},
                               "met": {"type": "boolean"}, "reason": {"type": "string"}}}}}}


def listing(r: Rubric) -> str:
    return "\n".join(f"- {c.id}" + (f" [{c.weight:g}]" if c.weight is not None else "")
                     + f": {c.text}" for c in r.criteria)


def score(r: Rubric, met: dict[str, bool]) -> float:
    """Weight met over the total positive weight; a met penalty criterion (negative weight)
    subtracts. Unweighted criteria weigh 1."""
    total = sum(max(c.weight if c.weight is not None else 1.0, 0.0) for c in r.criteria)
    if total <= 0:
        return 0.0
    got = sum((c.weight if c.weight is not None else 1.0) for c in r.criteria
              if met.get(c.id) and (c.weight is None or c.weight > 0))
    got -= sum(-c.weight for c in r.criteria
               if met.get(c.id) and c.weight is not None and c.weight < 0)
    return max(0.0, got / total)


@dataclass
class Attack:
    attacker: str                 # "white-box" | "black-box"
    deliverable: str
    how: str
    score: float
    met: dict[str, bool]
    reasons: dict[str, str]
    achieved: bool
    missing: str
    confidence: float
    rubric_model: str
    goal_model: str

    @property
    def disagree(self) -> bool:
        return self.score >= PASS and not self.achieved
