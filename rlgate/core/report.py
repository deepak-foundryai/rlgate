"""Result formats: the four-part finding, JSON (stable schema), the Markdown QA card, SARIF.

Everything renders from a ``TaskView`` — one checked task as the user sees it: the result,
its question-grouped outcome (core/questions.py), the run mode, and waiver state. The
terminal renderer (rlgate/render.py) and the HTML report (core/html.py) use the same view,
so every format says the same thing.

User-facing words follow the checklist's plain-label table: Proven / Needs your judgment,
Ready / Needs fixes / Broken, quick / full. The quality score (EQS) appears only in JSON and
the bank view.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rlgate import __version__
from rlgate.core import questions as Q
from rlgate.core import rules
from rlgate.core.finding import Finding, Severity, VerdictTier

if TYPE_CHECKING:
    from rlgate.pipeline import AuditResult

JSON_SCHEMA = "rlgate.check/1"
BANK_SCHEMA = "rlgate.bank/1"


# -- internal triage (KEEP/FIX/DROP) and the quality score -------------------------------

_WEIGHT = {Q.PROVEN: 0.1, Q.JUDGMENT: 0.04, Q.WARNING: 0.02}


def eqs(findings: list[Finding], baseline_sane: bool) -> float:
    """Quality score (EQS) in [0, 1]: 1.0 when clean. Each open finding costs its severity
    (low 1 … critical 4) times a weight: 0.1 proven, 0.04 needs your judgment, 0.02
    warning. Accepted (waived) findings cost nothing; a task that doesn't work scores 0."""
    if not baseline_sane:
        return 0.0
    penalty = sum(_WEIGHT.get(Q.kind_of(f), 0.0) * int(f.severity) for f in findings)
    return round(max(0.0, 1.0 - penalty), 2)


# -- the view -------------------------------------------------------------------------------

@dataclass
class TaskView:
    target: str                     # the path as the user typed it (used in commands)
    task_id: str
    root: Path                      # the task directory (waivers, file locations)
    result: AuditResult | None      # None when it could not run
    outcome: Q.Outcome | None
    mode: str = "full"              # "quick" | "full"
    cached: bool = False
    task_type: str = ""             # e.g. "harbor"
    stale: list = field(default_factory=list)      # waivers re-raised (files changed)
    error: str | None = None        # could not run: what happened and what to do
    created: str = ""
    standalone: bool = True         # `rlgate check <target>` works on this task by itself
    limit: str | None = None        # why rlgate can't do a full run of this task
    infra: bool = False             # could not run because of the backend, not the task

    @property
    def verdict(self) -> str:
        if self.outcome is not None:
            return self.outcome.verdict
        return Q.NOT_CHECKED if self.infra else Q.BROKEN

    @property
    def exit_code(self) -> int:
        return self.outcome.exit_code if self.outcome else 2

    @property
    def quality(self) -> float:
        if self.result is None:
            return 0.0
        sane = self.result.baseline_sane if self.mode == "full" else True
        return eqs(self.result.findings, sane)

    @property
    def findings(self) -> list[Finding]:
        return list(self.result.findings) if self.result is not None else []

    def cmd(self, *parts: str) -> str:
        return " ".join(["rlgate", *parts, shlex.quote(self.target)])


def verdict_slug(verdict: str) -> str:
    return {Q.READY: "ready", Q.NEEDS_FIXES: "needs_fixes",
            Q.NOT_CHECKED: "not_checked"}.get(verdict, "broken")



def tier_label(f: Finding) -> str:
    """PROVEN / NEEDS YOUR JUDGMENT — what kind of evidence backs the finding."""
    return "Proven" if f.verdict is VerdictTier.CONFIRMED else "Needs your judgment"


# -- the four-part finding --------------------------------------------------------------------

def four_part(f: Finding, view: TaskView | None = None, *, several: bool = False) -> dict:
    """What / Why / Proof / Fix. Proof is a reproduce command for findings proven by running
    the grader, and the file and line for file-only findings."""
    rule = rules.RULES.get(f.rule_id)
    title = rule.title if rule else f.title
    what = f.evidence.detail or f.title
    return {
        "rule": f.rule_id,
        "title": title,
        "what": _sentence(what),
        "why": rule.why if rule else "",
        "proof": proof(f, view, several=several),
        "fix": _sentence(f.fix_hint or (rule.fix if rule else "")),
    }


def proof(f: Finding, view: TaskView | None = None, *, several: bool = False) -> str:
    replay = f.evidence.replay or ""
    if replay.startswith("rlgate repro"):
        if view is None:
            return replay
        cmd = f"rlgate repro {f.rule_id} {shlex.quote(view.target)}"
        if several and "--proof" in replay:
            cmd += " --proof " + replay.split("--proof", 1)[1].split()[0]
        return cmd
    if replay.startswith("rlgate check") and view is not None:
        group = replay.split("--only", 1)[1].split()[0] if "--only" in replay else ""
        return view.cmd("check") + (f" --only {group}" if group else "")
    if f.location:
        return f.location
    return replay


def _sentence(s: str) -> str:
    s = (s or "").strip()
    first = s.split(None, 1)[0] if s else ""
    if first.isalpha() and first.islower() and first not in ("rlgate", "chmod", "pip"):
        s = s[0].upper() + s[1:]
    return s


def ordered(findings: list[Finding]) -> list[Finding]:
    order = {Q.PROVEN: 0, Q.JUDGMENT: 1, Q.WARNING: 2, Q.ACCEPTED: 3}
    return sorted(findings, key=lambda f: (order[Q.kind_of(f)], -int(f.severity), f.rule_id))


def several_of(f: Finding, findings: list[Finding]) -> bool:
    return sum(1 for g in findings if g.rule_id == f.rule_id) > 1


# -- JSON (stable schema) ------------------------------------------------------------------

def task_dict(view: TaskView) -> dict:
    d: dict = {
        "schema": JSON_SCHEMA,
        "rlgate": __version__,
        "task": view.task_id,
        "target": view.target,
        "type": view.task_type,
        "mode": view.mode,
        "cached": view.cached,
        "verdict": verdict_slug(view.verdict),
        "exit_code": view.exit_code,
    }
    if view.error is not None or view.result is None or view.outcome is None:
        d["error"] = view.error or "could not run"
        return d
    o, r = view.outcome, view.result
    b = r.baseline
    d.update({
        "seconds": round(getattr(r, "seconds", 0.0), 2),
        "quality_score": view.quality,
        "counts": {"proven": o.proven, "to_review": o.judgment, "warnings": o.warnings,
                   "accepted": o.accepted},
        "headline": o.headline(),
        "stopped_at_step": o.stopped_at,
        "complete": o.complete,
        "steps": [{"step": row.step.number, "name": row.step.label,
                   "question": row.step.text,
                   "rules": ", ".join(f"{x}xx" for x in row.step.ranges),
                   "status": row.word, "summary": row.summary, "cached": row.cached,
                   "seconds": round(row.seconds, 2)} for row in o.rows],
        "baseline": None if b is None else {
            "gold_passes": b.gold_passes, "do_nothing_fails": b.nop_fails,
            "runs": len(b.oracle_runs) or b.k, "gold_rewards": list(b.oracle_runs),
            "do_nothing_rewards": list(b.nop_runs), "flaky": sorted(b.flaky_checks),
            "fail_to_pass": b.fail_to_pass, "pass_to_pass": b.pass_to_pass,
            "broken_by_fix": b.broken_by_fix,
            "slowest_grading_seconds": round(b.grade_seconds, 1),
            "grading_limit_seconds": b.grade_limit,
            "extracted_patch_reward": b.extracted},
        "findings": [_finding_dict(f, view) for f in ordered(r.findings)],
        "stale_waivers": [w.rule for w in view.stale],
    })
    return d


def _finding_dict(f: Finding, view: TaskView) -> dict:
    p = four_part(f, view, several=several_of(f, view.findings))
    rule = rules.RULES.get(f.rule_id)
    return {
        **p,
        "step": rule.step.number if rule and rule.step else None,
        "step_name": rule.step.label if rule and rule.step else "",
        "kind": Q.kind_of(f) if not f.waiver else Q.ACCEPTED,
        "evidence": "proven" if f.verdict is VerdictTier.CONFIRMED else "judgment",
        "severity": f.severity.name.lower(),
        "location": f.location,
        "status": "accepted" if f.waiver else "open",
        "waiver": f.waiver,
    }


def to_json(view: TaskView) -> str:
    return json.dumps(task_dict(view), indent=2)


def bank_dict(views: list[TaskView], target: str) -> dict:
    counts = {"ready": 0, "needs_fixes": 0, "broken": 0, "not_checked": 0}
    for v in views:
        counts[verdict_slug(v.verdict)] += 1
    return {
        "schema": BANK_SCHEMA,
        "rlgate": __version__,
        "target": target,
        "tasks": len(views),
        "verdicts": counts,
        "common_problems": [{"rule": r, "title": t, "tasks": ids}
                            for r, t, ids in common_problems(views)],
        "results": [task_dict(v) for v in views],
    }


def common_problems(views: list[TaskView]) -> list[tuple[str, str, list[str]]]:
    """(rule, title, task ids) for every open problem, most widespread first."""
    hit: dict[str, set[str]] = {}
    for v in views:
        for f in v.findings:
            if not f.waiver:
                hit.setdefault(f.rule_id, set()).add(v.task_id)
    out = [(r, rules.RULES[r].title if r in rules.RULES else r, sorted(ids))
           for r, ids in hit.items()]
    return sorted(out, key=lambda x: (-len(x[2]), x[0]))


# -- Markdown: the QA card ----------------------------------------------------------------

def qa_card(view: TaskView) -> str:
    """The QA card that goes to labs with every sample (checklist, "QA card fields")."""
    L: list[str] = [f"# QA card: {view.task_id}", ""]
    if view.result is None or view.outcome is None:
        return "\n".join(L + [f"**Verdict:** Broken (could not run): {view.error}", ""])
    o, r = view.outcome, view.result
    top = o.top()
    reason = ""
    if top is not None and o.verdict != Q.READY:
        reason = f" ({top.rule_id}: {four_part(top, view)['title']})"
    L += [f"**Verdict:** {o.verdict}{reason}  ",
          f"**Steps:** {o.headline()}  ",
          f"**Counts:** {o.counts_line()}  ",
          (f"**Run:** {view.mode} run, rlgate {__version__}, "
          f"{view.created or time.strftime('%Y-%m-%d')}"), ""]

    L += ["| Field | Result |", "| --- | --- |"]
    for name, value in _card_fields(view):
        L.append(f"| {name} | {_md_cell(value)} |")
    L.append("")

    L += ["## Steps", "", "| Step | Question | Status | Result |", "| --- | --- | --- | --- |"]
    for row in o.rows:
        L.append(f"| {row.step.number} {row.step.label} | {row.step.text} | "
                 f"{_STATUS_WORD[row.status]} | {_md_cell(row.summary)} |")
    L.append("")

    fs = ordered(r.findings)
    if fs:
        L += ["## Findings", ""]
        for f in fs:
            p = four_part(f, view, several=several_of(f, fs))
            label = "Accepted" if f.waiver else tier_label(f)
            L += [f"### {f.rule_id} {p['title']}",
                  f"*{label} · {f.severity.name.lower()}*", "",
                  f"- **What:** {p['what']}", f"- **Why:** {p['why']}",
                  f"- **Proof:** `{p['proof']}`" if p["proof"] else "- **Proof:** -",
                  f"- **Fix:** {p['fix']}"]
            if f.waiver:
                w = f.waiver
                L.append(f"- **Accepted, with reason:** {w.get('reason')} "
                         f"({w.get('author')}, {w.get('date')})")
            L.append("")
    return "\n".join(L)


_STATUS_WORD = {Q.PASSED: "✓ passed", Q.PROVEN: "✗ failed",
                Q.JUDGMENT: "? to review", Q.WARNING: "! warning",
                Q.ACCEPTED: "✓ accepted, with reason", Q.SKIPPED: "- skipped",
                Q.NOT_YET: "- not checked yet", Q.NOT_RUN: "- not run",
                Q.PARTLY: "✓ partly checked"}


def _card_fields(view: TaskView) -> list[tuple[str, str]]:
    o, r = view.outcome, view.result
    assert o is not None and r is not None
    rows = {row.step: row for row in o.rows}
    b = r.baseline

    def by(*prefixes: str) -> list[Finding]:
        return [f for f in r.findings if f.rule_id.startswith(prefixes) and not f.waiver]

    if b is not None:
        k = len(b.oracle_runs) or b.k
        gates = (f"Reference {b.gold_passes}/{k}, do-nothing {k - b.nop_fails}/{k} passes"
                 if b.reference else
                 f"No reference solution; do-nothing {len(b.nop_runs) - b.nop_fails}/"
                 f"{len(b.nop_runs)} passes")
        if b.reports and b.per_test:
            gates += (f", {len(b.fail_to_pass)} fail-to-pass and {len(b.pass_to_pass)} "
                      "pass-to-pass tests")
        if b.extracted is not None:
            gates += f", extracted patch reward {b.extracted}"
        if b.flaky_checks:
            gates += f", flaky: {', '.join(sorted(b.flaky_checks))}"
    else:
        gates = "Not run (quick run: needs a full run)"
    leak = rows.get(rules.Step.LEAKS)
    gates += f"; leak scan: {leak.summary if leak else 'not run'}"
    grader = [f for f in by("RG1", "RG2") if not f.probe_id.startswith("review.")]
    probes = ("; ".join(f"{f.rule_id} {four_part(f, view)['title']}" for f in grader)
              if grader else
              ("No surviving mutations, forgeable verdicts or uncovered fix parts"
               if view.mode == "full" else "Not run (quick run)"))
    not_built = "Not checked yet"
    return [
        ("Verdict", f"{o.verdict}: {o.counts_line()}"),
        ("Build gates", gates),
        ("Grader probes", probes),
        ("Static review", _static_review(r, view)),
        ("Agentic audits", _agentic(r)),
        ("Hack agent", not_built + " (runs with --deep-review)"),
        ("Calibration", not_built),
        ("Dedup", not_built),
        ("Provenance", f"type: {view.task_type or '-'}; source: {view.target}"),
        ("Check trust", _trust(r)),
    ]


_REVIEW = (("review.statement", "statement"), ("review.tests", "tests"),
           ("review.reference", "reference fix"), ("review.verifier", "verifier command"),
           ("review.determinability", "determinability"),
           ("review.enforcement", "enforcement"), ("review.weak_pass", "weak pass"))


def _static_review(r, view: TaskView) -> str:
    """One entry per criterion group and per question, with the cited place."""
    from rlgate.pipeline import CheckRun

    checks = {c.probe_id: c for c in r.checks if c.probe_id.startswith("review.")}
    if not checks or all(c.status == CheckRun.SKIPPED_DEEP for c in checks.values()):
        return "Not checked yet (runs with --deep-review)"
    parts = []
    for pid, label in _REVIEW:
        c = checks.get(pid)
        if c is None:
            continue
        fs = [f for f in r.findings if f.probe_id == pid and not f.waiver]
        if fs:
            parts.append(f"{label}: " + ", ".join(
                f"{f.rule_id} at {f.location}" for f in fs))
        elif c.status == CheckRun.GOOD:
            parts.append(f"{label}: no problem")
        elif c.status == CheckRun.UNFINISHED:
            parts.append(f"{label}: didn't finish ({c.reason})")
        elif c.status == CheckRun.NA:
            parts.append(f"{label}: doesn't apply ({c.reason or 'not applicable'})")
        else:
            parts.append(f"{label}: not run")
    return "; ".join(parts)


def _agentic(r) -> str:
    """Confirmed false rejects (RG408), false accepts (RG110), hacks (L4) and rubric
    attacks, with what was run."""
    from rlgate.pipeline import CheckRun

    checks = {c.probe_id: c for c in r.checks if c.probe_id.startswith("agentic.")}
    ran = [c for c in checks.values() if c.status in (CheckRun.GOOD, CheckRun.FLAGGED)]
    if not ran:
        why = next((c.reason for c in checks.values() if c.status == CheckRun.UNFINISHED),
                   "")
        return f"Didn't finish ({why})" if why else \
            "Not checked yet (runs with --deep-review on a full run)"
    parts = []
    for pid, label, rids in (("agentic.ambiguity", "false rejects", ("RG408",)),
                             ("agentic.strength", "false accepts", ("RG110",)),
                             ("agentic.hack", "hacks", ("RG215", "RG111", "RG308", "RG113")),
                             ("agentic.rubric", "rubric attacks", ("RG112",))):
        c = checks.get(pid)
        if c is None or c.status not in (CheckRun.GOOD, CheckRun.FLAGGED):
            continue
        fs = [f for f in r.findings if f.rule_id in rids]
        what = "; ".join(f.evidence.detail.splitlines()[0] for f in fs) or "none found"
        parts.append(f"{label}: {what} ({c.note})" if c.note else f"{label}: {what}")
    return "; ".join(parts)


def _trust(r) -> str:
    from rlgate.pipeline import CheckRun

    ran = [c for c in r.checks if c.probe_id.startswith("review.")
           and c.status in (CheckRun.GOOD, CheckRun.FLAGGED)]
    if not ran:
        return "Every verdict above comes from deterministic checks (no LLM)"
    from rlgate.core.claims import summary
    measured = summary()
    how = (f"checked by execution so far, their claims held up as follows: {measured}" if
           measured else "their precision is not measured yet (no claim has been checked "
           "by execution)")
    return ("Static review verdicts come from LLM judges and need your judgment; "
            f"{how}. Every other verdict comes from deterministic checks or execution")


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def bank_markdown(views: list[TaskView], target: str) -> str:
    L = [f"# rlgate bank report: {target}", "",
         "| Task | Verdict | Proven | To review | Quality score |",
         "| --- | --- | --- | --- | --- |"]
    for v in views:
        o = v.outcome
        L.append(f"| {v.task_id} | {v.verdict} | {o.proven if o else '-'} | "
                 f"{o.judgment if o else '-'} | {v.quality:.2f} |")
    common = common_problems(views)
    if common:
        L += ["", "## Most common problems", ""]
        for r, t, ids in common:
            L.append(f"- **{r}** {t}: {len(ids)} task(s) ({', '.join(ids)})")
    L += ["", "---", ""]
    for v in views:
        L += [qa_card(v), ""]
    return "\n".join(L)


# -- SARIF 2.1.0 (GitHub code scanning annotations) ----------------------------------------

def sarif(views: list[TaskView]) -> str:
    used: dict[str, rules.Rule] = {}
    results = []
    for v in views:
        for f in v.findings:
            rule = rules.RULES.get(f.rule_id)
            if rule:
                used[rule.id] = rule
            p = four_part(f, v, several=several_of(f, v.findings))
            level = ("note" if f.waiver or Q.kind_of(f) == Q.WARNING else
                     "error" if Q.kind_of(f) == Q.PROVEN else "warning")
            res: dict = {
                "ruleId": f.rule_id,
                "level": level,
                "message": {"text": f"{p['title']}. {p['what']} Fix: {p['fix']}"},
                "locations": [{"physicalLocation": _sarif_location(f, v)}],
                "properties": {"task": v.task_id, "evidence": tier_label(f),
                               "severity": f.severity.name.lower(), "proof": p["proof"]},
            }
            if f.waiver:
                res["suppressions"] = [{"kind": "external",
                                        "justification": f.waiver.get("reason", "")}]
            results.append(res)
    driver = {
        "name": "rlgate", "version": __version__,
        "informationUri": "https://github.com/deepak-foundryai/rlgate",
        "rules": [{
            "id": r.id, "name": r.id,
            "shortDescription": {"text": r.title},
            "fullDescription": {"text": r.why},
            "help": {"text": f"{r.why} Fix: {r.fix} (rlgate explain {r.id})"},
            "properties": {"step": f"{r.step.number} {r.step.label}" if r.step else "",
                           "question": r.step.text if r.step else r.question.text,
                           "evidence": r.proof.value},
            "defaultConfiguration": {"level": "error" if r.severity >= Severity.HIGH
                                     else "warning"},
        } for r in sorted(used.values(), key=lambda r: r.id)],
    }
    return json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": driver}, "results": results}],
    }, indent=2)


def _sarif_location(f: Finding, v: TaskView) -> dict:
    path, line = None, None
    if f.location:
        head, _, tail = f.location.partition(":")
        if tail.split(":")[0].isdigit():
            line = int(tail.split(":")[0])
        for base in (v.root, v.root / "environment", v.root / "tests"):
            if (base / head).is_file():
                path = base / head
                break
    if path is None:
        for name in ("task.toml", "task.rlgate.yaml", "instruction.md"):
            if (v.root / name).is_file():
                path = v.root / name
                break
    uri = _relative(path or v.root)
    loc: dict = {"artifactLocation": {"uri": uri}}
    if line:
        loc["region"] = {"startLine": line}
    return loc


def _relative(p: Path) -> str:
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


# -- badge ------------------------------------------------------------------------------------

_BADGE_COLOUR = {Q.READY: "#2e7d32", Q.NEEDS_FIXES: "#b26a00", Q.BROKEN: "#c62828",
                 Q.NOT_CHECKED: "#757575"}


def badge_svg(views: list[TaskView]) -> str:
    """A README badge: ``rlgate | ready`` (the worst verdict across the tasks)."""
    order = [Q.BROKEN, Q.NEEDS_FIXES, Q.NOT_CHECKED, Q.READY]
    verdict = next((v for v in order if any(x.verdict == v for x in views)), Q.READY)
    label, value = "rlgate", verdict.lower()
    lw, vw = 6 * len(label) + 12, 7 * len(value) + 12
    w = lw + vw
    colour = _BADGE_COLOUR[verdict]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" '
            f'aria-label="{label}: {value}"><title>{label}: {value}</title>'
            f'<rect width="{lw}" height="20" fill="#555"/>'
            f'<rect x="{lw}" width="{vw}" height="20" fill="{colour}"/>'
            f'<g fill="#fff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" '
            f'font-size="11"><text x="{lw / 2}" y="14">{label}</text>'
            f'<text x="{lw + vw / 2}" y="14">{value}</text></g></svg>\n')
