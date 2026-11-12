"""Terminal views (Rich): the check output, a finding, a rule explanation, the bank view.

Layouts:

    rlgate check ./rate-limiter                         full run · 3m 12s

     Step 1  Build      ✓ passed    task builds (1 warning: base image not pinned)
     Step 2  Baseline   ✓ passed    gold passes 5/5, do-nothing fails 5/5
     ...
     Step 5  Cheating   - not run   fix step 4 first

     STOPPED AT STEP 4 of 6 · NEEDS FIXES

     RG101  A part of the fix is not tested                 PROVEN · high
       What:  ...
       Fix:   ...
       More:  rlgate explain RG101 ./rate-limiter

     After fixing, run: rlgate check ./rate-limiter   (resumes at step 4)

and the finding format (What / Why / Proof / Fix) under ``RG201  <title>  PROVEN · high``.
"""

from __future__ import annotations

from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from rlgate import ui
from rlgate.core import questions as Q
from rlgate.core import rules
from rlgate.core.finding import Finding
from rlgate.core.report import TaskView, common_problems, four_part, ordered, several_of, tier_label

HEADER_WIDTH = 72


def _header(left: str, right: str) -> Text:
    t = Text(left, style="bold")
    gap = max(2, HEADER_WIDTH - len(left) - len(right))
    t.append(" " * gap)
    t.append(right, style="dim")
    return t


def run_label(view: TaskView) -> str:
    secs = getattr(view.result, "seconds", 0.0) if view.result is not None else view.seconds
    label = f"{view.mode} run {ui.dot()} {ui.duration(secs)}"
    return label + (f" {ui.dot()} cached" if view.cached else "")


def verdict_text(verdict: str) -> Text:
    return Text(verdict.upper(), style=ui.VERDICT_STYLE.get(verdict, "bold"))


# -- one task ---------------------------------------------------------------------------------

def task(view: TaskView, *, command: str = "check") -> None:
    c = ui.out()
    c.print(_header(f"rlgate {command} {view.target}", run_label(view)))
    c.print()
    if view.result is None or view.outcome is None:
        c.print(Text.assemble(" ", ui.sym(ui.PROVEN), f" Could not run: {view.error}"),
                overflow="fold")
        c.print()
        c.print(Text.assemble(" ", verdict_text(view.verdict), "   could not run"))
        c.print()
        c.print(Text.assemble(" Next: ", ("rlgate doctor", "bold")))
        return
    o = view.outcome
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)                         # Step N
    grid.add_column(no_wrap=True)                         # name
    word_w = max(len(r.word) for r in o.rows) + 2
    grid.add_column(no_wrap=True, min_width=word_w)       # ✓ passed
    grid.add_column(overflow="fold")                      # what was verified / found
    name_w = max(len(r.step.label) for r in o.rows)
    for row in o.rows:
        dim = row.status in (Q.NOT_RUN, Q.SKIPPED, Q.NOT_YET)
        status = Text.assemble(ui.sym(row.status), " ",
                               (row.word, "dim" if dim else ui.style_of(row.status)))
        summary = Text(row.summary, style="dim" if dim else "")
        if row.cached:
            summary.append("  (from the last run)", style="dim")
        grid.add_row(Text(f"Step {row.step.number}", style="dim" if dim else "bold"),
                     Text(row.step.label.ljust(name_w), style="dim" if dim else ""),
                     status, summary)
        if ui.verbose() and not dim:
            grid.add_row(Text(""), Text(""), Text(""), Text(row.step.text, style="dim italic"))
            for mark, detail in _verbose_checks(view, row):
                grid.add_row(Text(""), Text(""), mark, detail)
    c.print(Padding(grid, (0, 0, 0, 1)))
    c.print()
    _questions(view)

    line = Text(" ")
    line.append(o.headline().replace("·", ui.dot()),
                style=ui.VERDICT_STYLE.get(o.verdict, "bold") if o.complete or o.stopped_at
                else "bold")
    line.append("   " + o.counts_line().replace("·", ui.dot()), style="dim")
    c.print(line, overflow="fold")

    accepted = [f for f in view.findings if f.waiver]
    for f in accepted:
        w = f.waiver or {}
        c.print(Text.assemble(" ", ui.sym(ui.ACCEPTED), f" {f.rule_id} accepted, with reason: ",
                              (f"\"{w.get('reason', '')}\"", "italic"),
                              (f" {ui.dash()} {w.get('author', '')}, {w.get('date', '')}",
                               "dim")), overflow="fold")
    for w in view.stale:
        c.print(Text.assemble(" ", ui.sym(ui.WARNING),
                              f" The waiver for {w.rule} was re-raised: "
                              f"{', '.join(w.files) or 'its files'} changed since it was "
                              "accepted"), overflow="fold")

    rows = ([r for r in o.rows if r.step.number == o.stopped_at] if o.stopped_at is not None
            else o.rows)
    open_ = [f for r in rows for f in ordered(r.findings)
             if Q.kind_of(f) in (Q.PROVEN, Q.JUDGMENT)]
    proven = _one_per_rule([f for f in open_ if Q.kind_of(f) == Q.PROVEN])
    for f in proven[:3]:
        c.print()
        c.print(_brief(f, view))
    rest = proven[3:] + _one_per_rule([f for f in open_ if Q.kind_of(f) == Q.JUDGMENT])
    if rest:
        c.print()
        c.print(Text(" Also found" if proven else " Found", style="bold"))
        for f in rest:
            title = rules.RULES[f.rule_id].title if f.rule_id in rules.RULES else f.title
            c.print(Text.assemble("  ", ui.sym(Q.kind_of(f)), f" {f.rule_id}  {title}"),
                    overflow="fold")
            c.print(Text(f"         {view.cmd('explain', f.rule_id)}", style="cyan"),
                    overflow="fold")
    c.print()
    if o.stopped_at is not None:
        c.print(Text.assemble(" After fixing, run: ", (view.cmd("check"), "bold"),
                              (f"   (resumes at step {o.stopped_at})", "dim")))
        return
    c.print(Text.assemble(" Next: ", (next_command(view), "bold")))


def _one_per_rule(fs: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    out = []
    for f in fs:
        if f.rule_id not in seen:
            seen.add(f.rule_id)
            out.append(f)
    return out


_ANSWER = {Q.YES: (Q.PASSED, "Yes"), Q.NO: (Q.JUDGMENT, "No"),
           Q.NO_PROVEN: (Q.PROVEN, "No"), Q.UNFINISHED: (ui.WARN, "Didn't finish"),
           Q.NOT_APPLICABLE: (Q.SKIPPED, "Doesn't apply"), Q.NOT_ASKED: (Q.NOT_RUN, "Not run")}


def _questions(view: TaskView) -> None:
    """The three reference-hidden questions, one answer each (shown when --deep-review asked them)."""
    got = Q.answers(view.result)
    if not got:
        return
    c = ui.out()
    c.print(Text(" The three questions (reference hidden, independent votes)", style="bold"))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)                                  # Q1
    grid.add_column(no_wrap=True,
                    min_width=max(len(_ANSWER[a.answer][1]) for a in got) + 2)   # ✓ Yes
    grid.add_column(overflow="fold")                               # question / detail
    for i, a in enumerate(got, 1):
        kind, word = _ANSWER[a.answer]
        grid.add_row(Text(f"Q{i}", style="bold"), ui.mark(kind, word), Text(a.question))
        votes = a.votes.replace(" calls (", " votes (").replace(" call (", " vote (")
        if a.answer == Q.YES:
            detail = a.detail + (f" {ui.dash()} {votes}" if votes else "")
        elif a.answer == Q.NO_PROVEN:
            detail = a.detail + " (proven by running it)"
        elif a.answer == Q.NO:
            detail = a.detail + " (needs your judgment)"
        else:
            detail = a.detail
        if detail:
            grid.add_row(Text(""), Text(""), Text(detail, style="dim"))
    c.print(Padding(grid, (0, 0, 0, 1)))
    c.print()


def _brief(f: Finding, view: TaskView) -> Padding:
    """The short form of a finding under a failed step: What, Fix, and where to see more."""
    p = four_part(f, view, several=several_of(f, view.findings))
    head = Table.grid(padding=(0, 3))
    head.add_column(overflow="fold")
    head.add_column(no_wrap=True, justify="right")
    head.add_row(Text(f"{f.rule_id}  {p['title']}", style="bold"),
                 Text(f"{tier_label(f).upper()} {ui.dot()} {f.severity.name.lower()}",
                      style=ui.style_of(Q.PROVEN)))
    body = Table.grid(padding=(0, 2))
    body.add_column(no_wrap=True, style="bold")
    body.add_column(overflow="fold")
    body.add_row("What:", p["what"])
    body.add_row("Fix:", p["fix"])
    body.add_row("More:", Text(view.cmd("explain", f.rule_id), style="cyan"))
    return Padding(Group(head, Padding(body, (0, 0, 0, 2))), (0, 0, 0, 1))


def next_command(view: TaskView) -> str:
    """The single next command to run."""
    o = view.outcome
    if o is None:
        return "rlgate doctor"
    top = o.top()
    if top is not None:
        return view.cmd("explain", top.rule_id)
    if view.mode == "quick" and not view.limit:
        return view.cmd("check") + "   (a full run builds and grades the task)"
    if not o.complete:
        return view.cmd("check") + "   (all six steps)"
    return view.cmd("report", "--format", "markdown") + "   (the QA card)"


def _verbose_checks(view: TaskView, row) -> list[tuple[Text, Text]]:
    """--verbose: one (status, check name + timing/reason) pair per check under its step."""
    from rlgate.pipeline import CheckRun, check_name

    out: list[tuple[Text, Text]] = []
    if view.result is None:
        return out
    if row.step is rules.Step.BASELINE:
        b = view.result.baseline
        if b is not None:
            out.append((Text(""), Text(f"rewards: gold {list(b.oracle_runs)}, do-nothing "
                                       f"{list(b.nop_runs)}", style="dim")))
            if b.reports and b.per_test:
                out.append((Text(""), Text(f"tests: {len(b.fail_to_pass)} fail-to-pass, "
                                           f"{len(b.pass_to_pass)} pass-to-pass, "
                                           f"{len(b.broken_by_fix)} broken by the fix",
                                           style="dim")))
            if b.grade_limit:
                out.append((Text(""), Text(f"slowest grading run {b.grade_seconds:.0f}s of "
                                           f"{b.grade_limit:.0f}s allowed", style="dim")))
    by_probe: dict[str, list] = {}
    for f in row.findings:
        by_probe.setdefault(f.probe_id, []).append(f)
    for c in view.result.checks:
        if Q.step_of_probe(c.probe_id) is not row.step:
            continue
        fs = by_probe.get(c.probe_id, [])
        if c.status == CheckRun.FLAGGED and fs:
            kinds = [Q.kind_of(f) for f in fs]
            status = next(k for k in (Q.PROVEN, Q.JUDGMENT, Q.WARNING, Q.ACCEPTED, Q.PASSED)
                          if k in kinds or k == Q.PASSED)
        else:
            status = {CheckRun.GOOD: ui.PASSED, CheckRun.FLAGGED: ui.JUDGMENT}.get(
                c.status, ui.SKIPPED)
        why = {CheckRun.SKIPPED_LIVE: "needs a full run",
               CheckRun.SKIPPED_BASELINE: "blocked by an earlier failure",
               CheckRun.SKIPPED_DEEP: "runs with --deep-review",
               CheckRun.UNFINISHED: "didn't finish",
               CheckRun.NA: "doesn't apply"}.get(c.status, "")
        if c.status == CheckRun.NA and getattr(c, "reason", ""):
            why = f"doesn't apply: {c.reason}"
        if c.status == CheckRun.UNFINISHED and getattr(c, "reason", ""):
            why = f"didn't finish: {c.reason}"
        elif getattr(c, "note", ""):
            why = c.note
        mark = Text("  ")
        mark.append_text(ui.sym(status))
        detail = Text(check_name(c.probe_id), style="dim")
        if c.seconds and c.seconds >= 0.05:
            detail.append(f"  {ui.duration(c.seconds)}", style="dim")
        if why:
            detail.append(f"  ({why})", style="dim")
        out.append((mark, detail))
    return out


# -- one finding: What / Why / Proof / Fix --------------------------------------------------

def finding(f: Finding, view: TaskView) -> Panel:
    p = four_part(f, view, several=several_of(f, view.findings))
    label = f"{tier_label(f).upper()} {ui.dot()} {f.severity.name.lower()}"
    if f.waiver:
        label += f" {ui.dot()} ACCEPTED"
    kind = Q.kind_of(f)
    head = Table.grid(padding=(0, 3), expand=True)
    head.add_column(overflow="fold", ratio=1)
    head.add_column(no_wrap=True, justify="right")
    head.add_row(Text(f"{f.rule_id}  {p['title']}", style="bold"),
                 Text(label, style=ui.style_of(kind if kind in (Q.PROVEN, Q.JUDGMENT,
                                                                Q.WARNING) else ui.PASSED)))

    body = Table.grid(padding=(0, 2))
    body.add_column(no_wrap=True, style="bold")
    body.add_column(overflow="fold")
    body.add_row("What:", p["what"])
    body.add_row("Why:", p["why"])
    body.add_row("Proof:", Text(p["proof"] or "-", style="cyan"))
    body.add_row("Fix:", p["fix"])
    if f.location and f.location != p["proof"]:
        body.add_row("Where:", f.location)
    if f.waiver:
        w = f.waiver
        body.add_row("Accepted:", f"\"{w.get('reason')}\" {ui.dash()} {w.get('author')}, "
                                  f"{w.get('date')}")
    return Panel(Group(head, Padding(body, (0, 0, 0, 2))), box=ui.box(),
                 width=min(ui.out().width, 96), padding=(0, 1))


def show(view: TaskView, rule_id: str) -> int:
    c = ui.out()
    fs = [f for f in ordered(view.findings) if f.rule_id == rule_id]
    if not fs:
        rule = rules.RULES[rule_id]
        c.print(Text.assemble(ui.mark(ui.PASSED, "not found"),
                              f"  {rule_id} ({rule.title}) was not found on "
                              f"{view.task_id} in the last {view.mode} run."))
        c.print(Text(f"  What it checks: rlgate explain {rule_id}", style="dim"))
        return 0
    for f in fs:
        c.print(finding(f, view))
    tail = [f"rlgate explain {rule_id}   what the rule means, with examples"]
    if Q.kind_of(fs[0]) == Q.JUDGMENT:
        tail.append(view.cmd("waive", rule_id) + ' --reason "..."   accept it, with a reason')
    for t in tail:
        c.print(Text(f"  {t}", style="dim"))
    return 0


# -- explain -------------------------------------------------------------------------------

def explain(rule_id: str) -> None:
    from rlgate.core.rule_docs import DOCS

    r = rules.RULES[rule_id]
    meaning, bad, good = DOCS[rule_id]
    lexer = "docker" if rule_id.startswith("RG6") else \
        "bash" if rule_id in ("RG301", "RG302", "RG303", "RG304", "RG305") else "python"
    head = Text(f"{r.id}  {r.title}", style="bold")
    where = f"Step {r.step.number} {r.step.label}" if r.step else r.question.text
    sub = Text(f"{where}  {ui.dot()}  {r.proof.value}  {ui.dot()}  "
               f"{r.severity.name.lower()} severity", style="dim")

    def block(title: str, code: str, status: str) -> Group:
        return Group(Text.assemble(ui.sym(status), f" {title}", style="bold"),
                     Padding(Syntax(code, lexer, theme="ansi_dark", word_wrap=True,
                                    background_color="default"), (0, 0, 0, 2)))

    waive = ("Proven high-severity findings can't be waived, only fixed."
             if r.proof is rules.Proof.PROVEN and r.severity >= rules.Severity.HIGH
             else f'If it is acceptable here: rlgate waive {r.id} <task> --reason "..."')
    parts = [head, sub, Text(""), Text(meaning), Text(""),
             Text.assemble(("Why it matters: ", "bold"), r.why), Text(""),
             block("Bad", bad, ui.PROVEN), Text(""), block("Good", good, ui.PASSED), Text(""),
             Text.assemble(("Fix: ", "bold"), r.fix),
             Text(waive, style="dim")]
    ui.out().print(Panel(Group(*parts), box=ui.box(), expand=False, padding=(0, 1)))


# -- bank view -----------------------------------------------------------------------------

def bank(views: list[TaskView], target: str, seconds: float, mode: str) -> None:
    c = ui.out()
    c.print(_header(f"rlgate check {target}",
                    f"{len(views)} tasks {ui.dot()} {mode} run {ui.dot()} "
                    f"{ui.duration(seconds)}"))
    c.print()
    t = Table(box=ui.box(), show_edge=False, pad_edge=False, header_style="bold")
    t.add_column("Task", no_wrap=True, overflow="ellipsis", min_width=18)
    t.add_column("Verdict", no_wrap=True)
    t.add_column("Proven", justify="right")
    t.add_column("To review", justify="right")
    t.add_column("Quality score", justify="right")
    order = {Q.BROKEN: 0, Q.NEEDS_FIXES: 1, Q.NOT_CHECKED: 2, Q.READY: 3}
    for v in sorted(views, key=lambda v: (order.get(v.verdict, 0), v.task_id)):
        o = v.outcome
        status = {Q.READY: ui.PASSED, Q.NEEDS_FIXES: ui.WARNING,
                  Q.NOT_CHECKED: ui.NOT_RUN}.get(v.verdict, ui.PROVEN)
        verdict = Text.assemble(ui.sym(status), " ", (v.verdict, ui.style_of(status)))
        if o is None:
            verdict.append(" (could not run)", style="dim")
        t.add_row(v.task_id, verdict, str(o.proven) if o else "-",
                  str(o.judgment) if o else "-", f"{v.quality:.2f}")
    c.print(t)

    common = common_problems(views)
    if common:
        c.print()
        c.print(Text("Most common problems", style="bold"))
        for rid, title, ids in common[:8]:
            shown = ", ".join(ids[:3]) + (f" +{len(ids) - 3} more" if len(ids) > 3 else "")
            c.print(Text.assemble(f"  {rid}  ", title, (f"  {len(ids)} of {len(views)} tasks",
                                                        "bold")), overflow="fold")
            c.print(Text(f"         {shown}", style="dim"), overflow="ellipsis", no_wrap=True)
    for title, status, errors in (
            ("Could not run", ui.PROVEN,
             [v for v in views if v.result is None and not v.infra]),
            (("Not checked (the backend failed, not the task; rerun the same command "
             "without --fresh to redo only these)"), ui.NOT_RUN,
             [v for v in views if v.result is None and v.infra])):
        if errors:
            c.print()
            c.print(Text(title, style="bold"))
            for v in errors:
                c.print(Text.assemble("  ", ui.sym(status), f" {v.task_id}: {v.error}"),
                        overflow="fold")

    n = {k: sum(1 for v in views if v.verdict == k) for k in (Q.READY, Q.NEEDS_FIXES,
                                                              Q.BROKEN, Q.NOT_CHECKED)}
    c.print()
    line = Text(" VERDICT: ")
    worst = next((k for k in (Q.BROKEN, Q.NEEDS_FIXES, Q.NOT_CHECKED) if n[k]), Q.READY)
    line.append_text(verdict_text(worst))
    line.append(f"   {n[Q.READY]} ready {ui.dot()} {n[Q.NEEDS_FIXES]} need fixes {ui.dot()} "
                f"{n[Q.BROKEN]} broken")
    if n[Q.NOT_CHECKED]:
        line.append(f" {ui.dot()} {n[Q.NOT_CHECKED]} not checked")
    c.print(line)
    c.print()
    worst_view = next((v for v in sorted(views, key=lambda v: (order.get(v.verdict, 0),
                                                               v.task_id))
                       if v.verdict != Q.READY and v.standalone), None)
    report = "rlgate report --format html --output report.html"
    if worst_view:
        nxt, why = worst_view.cmd("check"), ""
    elif mode == "quick":               # file checks only: the full run is what's left
        nxt, why = f"rlgate check {target}", "   the full run: builds, grades, probes"
    else:
        nxt, why = report, "   a filterable report to share"
    c.print(Text.assemble(" Next: ", (nxt, "bold"), (why, "dim")))
    if nxt != report:
        c.print(Text(f"       {report}   a filterable report to share", style="dim"))


# -- rules + waivers -----------------------------------------------------------------------

def rules_table(only: rules.Step | None = None) -> None:
    c = ui.out()
    for st, rs in rules.by_step().items():
        if only is not None and st is not only:
            continue
        ranges = ", ".join(f"{r}xx" for r in st.ranges)
        c.print(Text.assemble((f"Step {st.number}  {st.label}", "bold"), f"  {st.text}",
                              (f"  {ranges}  --only {st.slug}", "dim")))
        for r in rs:
            c.print(Text.assemble(f"  {r.id}  ", r.title, "  ",
                                  (f"{r.proof.value} {ui.dot()} {r.severity.name.lower()}",
                                   "dim")), overflow="fold")
        c.print()
    if only is None:
        c.print(Text("Optional, after all six steps pass", style="bold"))
        c.print(Text("  Difficulty (RG8xx, rlgate calibrate) and duplicates across a bank "
                     "(RG9xx): not checked yet", style="dim"))
        c.print()
    c.print(Text("  rlgate explain <rule>   what a rule means, with a bad and a good example",
                 style="dim"))
