"""The L3 agent loop and its prompts.

A plain tool-calling loop over LiteLLM: the model answers, every tool call it makes is run
on the bench or the judge, the results go back, until it calls ``finish`` or reaches
``max_turns``.

Cost control: the conversation is re-sent every turn, so old tool outputs are replaced by a
one-line placeholder once more than ``KEEP + STEP`` are in it (observation masking: in
published agent studies it halves cost without hurting solve rates). Masking happens in
batches of ``STEP`` so the unchanged prefix of the conversation can be served from the
provider's prompt cache between batches. The agent's own messages (its reasoning and tool
calls) are never masked. ``turn`` makes one model call (``Session`` supplies it, so the budget, the
cost and the author-family rule apply to the agent exactly as to the L2 judges).
"""

from __future__ import annotations

from collections.abc import Callable

from rlgate.agentic.judges import AmbiguityJudge, StrengthJudge, _Judge
from rlgate.llm import ChatTurn

NUDGES = 2                      # replies without a tool call before the loop gives up
KEEP = 8                        # tool outputs kept in full; older ones are masked
STEP = 6                        # mask this many at once, so the cached prefix stays stable
MASKED = "[output removed to save space: call {name} again if you still need it]"

SYSTEM = """You audit tasks used to train coding agents with reinforcement learning. A task \
is a statement given to a solver, a repository, and a verifier that grades the solver's \
change. You work only through the tools. You never change the task's files: you write \
candidate implementations, which are applied to a fresh copy of the repository when run or \
graded. Never write or change test files. Be concrete and economical: every grading is \
expensive, so check your candidate with `run` before you grade it."""

STRENGTH = """Question: is the verifier too WEAK? Could an incomplete or incorrect \
implementation still pass?

You see what a solver sees: the statement and the pre-change repository. You do not see the \
tests or the reference fix, and you must not try to guess them from grading results.

1. Read the statement and explore the repository (list_dir, read_file, search).
2. Call record_requirements ONCE with every independently testable requirement, each with a \
verbatim quote from the statement (source "statement") or from a repository file (source = \
its path). This list is frozen when you first grade.
3. For each requirement, ask: would violating it make the verifier fail? Pick the ones a \
verifier is most likely to miss. Write a candidate that is otherwise a reasonable \
implementation but breaks that requirement (or does only part of the work), check it with \
`run`, then call try_bypass with a check_cmd that exits 0 only when the requirement is met, \
for example: python3 -c "import shop; assert shop.double(3) == 6"
4. {leads}
5. You have {grades} gradings. Call finish when you are done or out of ideas.

STATEMENT:
{statement}

REPOSITORY (top level):
{tree}{dossier}"""

AMBIGUITY = """Question: is the verifier too STRICT? Does it reject correct solutions that \
the statement allows?

You may read the statement, the pre-change repository and the tests (list_tests, \
read_test). You do not see the reference fix.

1. For every requirement the tests enforce, find what in the statement justifies it.
2. Where a test pins a detail the statement leaves open (a name, message, format, \
structure, order, side effect), first search the repository's code, docs and existing \
interfaces: if they tell a solver the detail, it is not an ambiguity.
3. {leads}
4. Where an ambiguity remains, prove it: write a COMPLETE implementation that follows the \
statement but makes a different reasonable choice on that one point, check it with `run`, \
and call try_alternative. It must fail only the tests about that point \
(expected_failures) and pass every other test; check_cmd must exit 0 when the stated \
requirement is met, on your implementation and on the reference.
5. You have {grades} gradings. Call finish when you are done.

STATEMENT:
{statement}

REPOSITORY (top level):
{tree}{dossier}"""

LEADS_STRENGTH = ("A first-pass review suggested these shortcuts. Try each one first, "
                  "passing its id as lead:\n{items}")
LEADS_AMBIGUITY = ("A first-pass review flagged these tested details as impossible to know. "
                   "Settle each one with resolve_lead (with a quote from the repository when "
                   "context resolves it), or prove it with try_alternative and its id as "
                   "lead:\n{items}")


def prompt(judge: _Judge, statement: str, tree: list[str], dossier: str = "") -> str:
    strength = isinstance(judge, StrengthJudge)
    items = "\n".join(f"- {lid}: {what}" for lid, what in judge.leads)
    if items:
        leads = (LEADS_STRENGTH if strength else LEADS_AMBIGUITY).format(items=items)
    else:
        leads = "No earlier leads."
    body = STRENGTH if strength else AMBIGUITY
    assert strength or isinstance(judge, AmbiguityJudge)
    listing = "\n".join(tree[:200]) + (f"\n… {len(tree) - 200} more" if len(tree) > 200
                                        else "")
    extra = (f"\n\nOUTLINE OF THE FILES THE STATEMENT AND TESTS POINT AT (read_symbol for a "
             f"body):\n{dossier}") if dossier else ""
    return body.format(leads=leads, grades=judge.max_grades, statement=statement.strip(),
                       tree=listing, dossier=extra)


def dossier(bench, paths: list[str], limit: int = 6000) -> str:
    """Signatures of the files a solver would open first: a cheap map to start from."""
    out, size = [], 0
    for rel in paths:
        try:
            syms = bench._symbols(rel)
        except Exception:                     # noqa: BLE001, S112 - a map, never a failure
            continue
        if not syms:
            continue
        block = f"{rel}\n" + "\n".join(("    " if s.parent else "  ") +
                                        f"{s.line}  {s.signature[:160]}" for s in syms)
        if size + len(block) > limit:
            break
        out.append(block)
        size += len(block)
    return "\n".join(out)


def mask(messages: list[dict]) -> int:
    """Replace old tool outputs with a placeholder; returns how many were masked now."""
    live = [m for m in messages if m.get("role") == "tool" and not m.get("_masked")]
    if len(live) <= KEEP + STEP:
        return 0
    n = 0
    for m in live[:len(live) - KEEP]:
        m["content"] = MASKED.format(name=m.get("name", "the tool"))
        m["_masked"] = True
        n += 1
    return n


def run(judge: _Judge, turn: Callable[[list[dict], list[dict]], ChatTurn], user: str, *,
        max_turns: int, system: str = SYSTEM) -> None:
    """Drive the agent until it finishes or runs out of turns. Errors from ``turn``
    propagate (the caller decides they mean "could not judge")."""
    budget = (f"\n\nYou have {max_turns} turns in all, and every tool call round uses "
              f"one. Read only what you need: start writing a candidate by turn "
              f"{max(2, max_turns // 2)}.")
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": user + budget}]
    tools = judge.specs()
    nudges = 0
    warn_at = max(1, max_turns - max(3, max_turns // 4))
    for i in range(max_turns):
        judge.audit.turns = i + 1
        if i == warn_at and judge.audit.grades == 0:
            messages.append({"role": "user", "content": (
                f"{max_turns - i} turns left and nothing graded yet. Stop exploring: write "
                "your best candidate now, check it with run, and grade it.")})
        t = turn(messages, tools)
        messages.append(t.message)
        if not t.calls:
            nudges += 1
            if nudges > NUDGES:
                return
            messages.append({"role": "user", "content": "Use the tools to continue; call "
                             "finish when you are done."})
            continue
        for call_id, name, args in t.calls:
            out = judge.call(name, args)
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name,
                             "content": out})
        if judge.done:
            return
        mask(messages)
