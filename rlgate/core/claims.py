"""How often LLM claims hold up when they are checked by execution.

L2 judges claim things by reading files (a weak pass exists; a tested detail can't be known).
L3 then runs them: a shortcut the verifier passes confirms the claim, one it rejects refutes
it; a determinability gap the repository answers is refuted, one a rejected alternative
proves is confirmed. Every outcome is appended to ``<state>/claims.jsonl``; the precision per
question is what the report shows instead of "not measured".

A re-check of the same claim replaces its earlier outcome (keyed by task, question, claim).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CONFIRMED, REFUTED, UNSETTLED = "confirmed", "refuted", "unsettled"


def _path() -> Path:
    from rlgate.core.repro import state_dir
    return state_dir() / "claims.jsonl"


def log(task_id: str, question: str, claim: str, outcome: str, how: str = "") -> None:
    key = hashlib.sha1(f"{task_id}\0{question}\0{claim}".encode()).hexdigest()[:16]
    rec = {"key": key, "task": task_id, "question": question, "claim": claim[:400],
           "outcome": outcome, "how": how, "at": int(time.time())}
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def tally(path: Path | None = None) -> dict[str, dict[str, int]]:
    """question → {confirmed, refuted, unsettled}, the latest outcome of each claim."""
    p = path or _path()
    latest: dict[str, dict] = {}
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            r = json.loads(line)
            latest[r["key"]] = r
        except (ValueError, KeyError, TypeError):
            continue
    out: dict[str, dict[str, int]] = {}
    for r in latest.values():
        q = out.setdefault(r["question"], {CONFIRMED: 0, REFUTED: 0, UNSETTLED: 0})
        if r["outcome"] in q:
            q[r["outcome"]] += 1
    return out


def summary(path: Path | None = None) -> str:
    """One line: precision per question, from claims execution settled."""
    parts = []
    for q, t in sorted(tally(path).items()):
        settled = t[CONFIRMED] + t[REFUTED]
        if not settled:
            continue
        parts.append(f"{q.replace('_', ' ')}: {t[CONFIRMED]} of {settled} held up "
                     f"({t[CONFIRMED] / settled:.0%})")
    return "; ".join(parts)
