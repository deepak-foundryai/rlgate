"""The L2 judges: what each one is asked, and the structured answer it must return.

First pass (sees the reference fix): each artifact judged against its own criteria, one call
per artifact. Second pass (reference hidden): three questions, each asked in ``votes``
independent calls (fresh context, different seeds, models taking turns through the panel).

Every answer is a JSON object validated against a schema (``rlgate.llm``): verdict, the
criterion, the test, the quoted text and where it is from, the specific gap, a confidence,
and what to change. ``review/cite.py`` then checks every negative against the real files.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    name: str
    rule: str                      # the rule a valid negative becomes
    text: str                      # what the judge checks
    sources: tuple[str, ...]       # files a quote may come from
    needs_test: bool = False       # a negative must name a test


# -- first pass: each artifact against its own criteria ---------------------------------

FIRST: dict[str, tuple[Criterion, ...]] = {
    "statement": (
        Criterion("actionable", "RG701",
                  "The statement describes a concrete, actionable change: a solver can tell "
                  "what to build or change and when it is done.", ("statement",)),
        Criterion("no_leak", "RG702",
                  "The statement does not leak the solution: no code from the reference "
                  "fix, no exact lines to change, no step-by-step recipe the solver only "
                  "has to copy. Naming the file or function to work on is fine.",
                  ("statement",)),
        Criterion("self_contained", "RG703",
                  "The statement does not depend on material the solver cannot see: "
                  "attachments, images, screenshots, linked issues, chat threads or "
                  "discussions that are not in the statement or the repository.",
                  ("statement",)),
    ),
    "tests": (
        Criterion("agrees", "RG405",
                  "Every test checks something the statement asks for. No test requires "
                  "behaviour the statement contradicts or never asks for.",
                  ("tests",), needs_test=True),
        Criterion("meaningful", "RG406",
                  "The tests assert meaningful behaviour: real outputs, state or effects of "
                  "the change, not only that code imports, runs, or returns something.",
                  ("tests",), needs_test=True),
        Criterion("accepts_equivalents", "RG407",
                  "The tests accept every correct implementation: they do not pin details "
                  "of the reference fix the statement leaves open (exact wording of a "
                  "message, internal names, call order, formatting, a data structure).",
                  ("tests",), needs_test=True),
    ),
    "reference": (
        Criterion("relevant", "RG105",
                  "Every part of the reference fix serves the requested change; nothing "
                  "unrelated is bundled in.", ("reference",)),
        Criterion("honest", "RG106",
                  "The reference fix has no hardcoded answers (special-casing the tests' "
                  "inputs or expected values) and does not edit test files.",
                  ("reference",)),
    ),
    "verifier": (
        Criterion("honest", "RG107",
                  "The verifier command does not manufacture success (writes a passing "
                  "reward regardless), suppress failures (`|| true`, ignored exit codes, "
                  "swallowed errors, a pass on a timeout), or substitute superficial "
                  "checks (a file exists, a grep for a string) for running the tests.",
                  ("verifier",)),
    ),
}

# -- second pass: the reference hidden, three questions ---------------------------------

SECOND: dict[str, Criterion] = {
    "determinability": Criterion(
        "determinability", "RG704",
        "Can every tested requirement be determined from what the solver sees: the "
        "statement AND the pre-change repository (its code, docs, existing tests and "
        "conventions)? Tests often check edge cases the statement never spells out; that is "
        "fine when the repository shows them (an existing error message or constant, a "
        "sibling function that handles the same case, a documented format, the project's "
        "naming pattern) or a careful engineer working in this repository would infer them. "
        "Read REPOSITORY EVIDENCE FOR TESTED DETAILS before reporting: it lists where the "
        "repository shows each tested detail the statement does not mention. A problem is "
        "only an assertion that checks a name, value, format, message, status code or "
        "behaviour that neither the statement nor the repository states or clearly implies; "
        "in 'gap', name the detail and say where in the repository you looked for it.",
        ("tests",), needs_test=True),
    "enforcement": Criterion(
        "enforcement", "RG108",
        "Do the tests enforce the requested behaviour? A problem is a requirement in the "
        "statement that no test checks, or checks so loosely that breaking it still passes. "
        "Quote the requirement from the statement, or the loose assertion from the tests.",
        ("statement", "tests")),
    "weak_pass": Criterion(
        "weak_pass", "RG109",
        "Could a trivial or incomplete implementation pass? A problem is a concrete cheap "
        "implementation (hardcoding the tested values, special-casing the test inputs, "
        "doing only part of the change, stubbing the hard part) that every test would "
        "accept. Describe that implementation exactly and quote the assertion it gets past.",
        ("tests",), needs_test=True),
}

ARTIFACTS = tuple(FIRST)
QUESTIONS = tuple(SECOND)

SYSTEM = """You review tasks used to train and evaluate coding agents with reinforcement \
learning. Each task has a statement given to the solver, tests and a verifier that grade \
the solver's change, and sometimes the author's reference fix. You read files only; you \
run nothing.

Report a problem only when you can show it:
- Copy the quote character for character from ONE file in the bundle, and say which one. \
Keep it short: the one line or assertion that shows the problem. Never paraphrase a quote.
- When the problem is about a test, name it exactly as it is defined (for example \
tests/test_api.py::test_rejects_negative or test_rejects_negative).
- State the gap concretely: name the identifier, value, message, status code or behaviour. \
"Could be clearer" or "more detail needed" is not a gap.
- Confidence is your probability, 0 to 1, that a careful human reviewer would agree.
Do not report a problem you are unsure about just to be safe: a false alarm costs a good \
task. When nothing is wrong, say so."""


def first_prompt(artifact: str, rendered: str) -> str:
    crit = FIRST[artifact]
    lines = "\n".join(f"- {c.name}: {c.text}" for c in crit)
    return (f"{rendered}\n\nJudge the {artifact.upper()} above against each criterion:\n"
            f"{lines}\n\nReturn one entry per criterion with no problem (verdict pass, other "
            "fields empty, confidence your certainty), and one entry per problem you can "
            "show (verdict fail). In 'fix', say what the author should change.")


def second_prompt(question: str, rendered: str) -> str:
    c = SECOND[question]
    extra = (" In 'detail', describe the cheap implementation exactly (what it returns or "
             "does)." if question == "weak_pass" else
             " In 'detail', say what the author should change.")
    return (f"{rendered}\n\nThe author's reference fix is deliberately withheld: judge only "
            f"from what the solver sees.\n\nQuestion: {c.text}\n\nAnswer 'ok' when there is "
            "no problem, or 'problem' with every problem you can show in 'negatives'."
            + extra)


def rewrite_prompt(rendered: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"{rendered}\n\nThe TASK STATEMENT above has these problems:\n{listed}\n\n"
            "Rewrite the statement so a solver can do the task from it and the repository "
            "alone: state every tested requirement the problems name, remove references to "
            "material the solver cannot see, and keep everything else. Do not add code, "
            "the exact lines to change, or a step-by-step recipe: the solver must still "
            "solve it. Return the full new statement.")


def first_schema(artifact: str) -> dict:
    crit = FIRST[artifact]
    sources = sorted({s for c in crit for s in c.sources})
    return _obj({"results": {"type": "array", "minItems": 1, "items": _obj({
        "criterion": {"type": "string", "enum": [c.name for c in crit]},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "test": {"type": "string"},
        "quote": {"type": "string"},
        "quote_from": {"type": "string", "enum": sources + [""]},
        "gap": {"type": "string"},
        "confidence": {"type": "number"},
        "fix": {"type": "string"},
    })}})


def second_schema(question: str) -> dict:
    c = SECOND[question]
    return _obj({
        "verdict": {"type": "string", "enum": ["ok", "problem"]},
        "negatives": {"type": "array", "items": _obj({
            "test": {"type": "string"},
            "quote": {"type": "string"},
            "quote_from": {"type": "string", "enum": list(c.sources)},
            "gap": {"type": "string"},
            "confidence": {"type": "number"},
            "detail": {"type": "string"},
        })},
    })


REWRITE_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["statement"],
    "properties": {"statement": {"type": "string"}},
}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props),
            "properties": props}
