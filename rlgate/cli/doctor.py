"""``rlgate doctor``: can this machine run full checks, and what is missing.

With a folder it also shows how each task would run (the runtime precheck): nothing is
started and nothing is graded.
"""

from __future__ import annotations

import os

from rich.table import Table
from rich.text import Text

from rlgate import settings as S
from rlgate import ui


def _home(path: str | None) -> str:
    if not path:
        return "?"
    if not os.path.isabs(path):
        return "./" + path
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


PROVIDER_KEYS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
                 "GROQ_API_KEY", "TOGETHERAI_API_KEY", "TOGETHER_API_KEY",
                 "FIREWORKS_AI_API_KEY", "AZURE_API_KEY")


def _provider_keys() -> list[str]:
    """Model provider keys set in the shell or a .env (names only, never values)."""
    from rlgate.config import load_dotenv
    load_dotenv()
    return [k for k in PROVIDER_KEYS if os.environ.get(k)]


def _where(key: str) -> str:
    from rlgate.config import origin
    o = origin(key)
    return "your shell" if o == "shell" else _home(o)


def doctor(*, config: str | None, model: str | None, ping: bool, options: bool,
           target: str | None) -> int:
    from rlgate.config import origin
    from rlgate.llm import LLM, LLMError, family, same_family
    from rlgate.sandbox.daytona import DaytonaOptions
    from rlgate.sandbox.daytona import unavailable_reason as daytona_reason
    from rlgate.sandbox.docker import unavailable_reason as docker_reason

    try:
        s = S.load(config, model=model, near=target)
    except S.SettingsError as e:
        ui.error(str(e), "fix rlgate.toml (rlgate init shows every option)")
        return 2
    if options:
        return _options(DaytonaOptions)

    c = ui.out()
    failed = False
    c.print(Text.assemble(("rlgate doctor", "bold"), (f"   settings: {s.source}", "dim")))
    c.print()
    c.print(Text("Where full checks run", style="bold"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    yr, dr = daytona_reason(), docker_reason()
    if yr is None:
        key_from = origin("DAYTONA_API_KEY")
        src = "your shell" if key_from == "shell" else _home(key_from)
        grid.add_row(ui.mark(ui.OK, "ready"), "Daytona", f"cloud sandboxes {ui.dot()} key "
                                                         f"from {src}")
        if ping:
            try:
                from daytona import Daytona

                from rlgate.sandbox.daytona_quota import Budget
                client = Daytona()
                next(iter(client.list()), None)
                grid.add_row("", "", ui.mark(ui.OK, "key accepted by the Daytona API"))
                grid.add_row("", "", ui.mark(ui.INFO, "account limits: "
                                             + Budget(client).describe()))
            except Exception as e:  # noqa: BLE001 - report, don't crash the doctor
                failed = True
                grid.add_row("", "", ui.mark(ui.FAIL, f"API call failed: {e}"))
    else:
        grid.add_row(ui.mark(ui.WARN, "not set up"), "Daytona",
                     f"{yr}. Set DAYTONA_API_KEY in your shell or .env to use it")
    if dr is None:
        grid.add_row(ui.mark(ui.OK, "ready"), "Docker", "containers on this machine")
    else:
        word = "not installed" if "not installed" in dr else "not running"
        why = ("optional: Daytona already runs containers" if yr is None
               else "start Docker Desktop, or set up Daytona")
        grid.add_row(ui.mark(ui.WARN, word), "Docker", why)
    c.print(grid)

    chosen = s.environment.type
    c.print()
    if chosen == "auto":
        where = "Daytona" if yr is None else "Docker" if dr is None else None
        if where:
            c.print(Text.assemble("  Full checks of container tasks run on ", (where, "bold"),
                                  (" (auto; change it with [environment] type in rlgate.toml)",
                                   "dim")))
        else:
            c.print(Text.assemble("  ", ui.mark(ui.WARN, "no container backend"),
                                  ": checks run in quick mode (file-only) until Docker or "
                                  "Daytona is ready"))
    else:
        bad = {"daytona": yr, "docker": dr}.get(chosen)
        if bad:
            failed = True
            c.print(Text.assemble("  ", ui.mark(ui.FAIL, "not usable"),
                                  f": rlgate.toml selects {chosen}, but {bad}"))
        else:
            c.print(Text.assemble("  Every full check runs on ", (chosen, "bold"),
                                  (f" (from {s.source})", "dim")))
    if s.environment.kwargs and chosen in ("daytona", "auto"):
        try:
            DaytonaOptions.from_kwargs(s.environment.kwargs)
        except ValueError as e:
            failed = True
            c.print(Text.assemble("  ", ui.mark(ui.FAIL, "bad setting"), f": {e}"))

    c.print()
    c.print(Text.assemble(("Models", "bold"),
                          (("  optional: only --deep-review (the LLM review) uses them; any "
                           "LiteLLM provider/model"), "dim")))
    mg = Table.grid(padding=(0, 2))
    mg.add_column(no_wrap=True)
    mg.add_column(no_wrap=True)
    mg.add_column(overflow="fold")
    roles = [("judge", s.models.judge), ("second opinion", s.models.second_opinion)]
    if s.models.review:
        roles = [(f"review judge {i + 1}", m) for i, m in enumerate(s.models.review)]
    keys = _provider_keys()
    if keys:
        mg.add_row(ui.mark(ui.OK, "found"), "keys",
                   ", ".join(f"{k} ({_where(k)})" for k in keys))
    router = "OPENROUTER_API_KEY" in keys
    for role, m in roles:
        if not m:
            mg.add_row(ui.mark(ui.INFO, "not set"), role, Text("-", style="dim"))
            continue
        llm = LLM.from_settings(s.models, model=m)
        reason = llm.check()
        if reason is not None:
            hint = reason.split(" to use ")[0] if " to use " in reason else reason
            hint = f"{m}: {hint} to enable it"
            if router and not m.startswith("openrouter/"):
                hint += (f"; or, with your OpenRouter key, name it "
                         f"openrouter/{family(m)}/<model> (rlgate.toml or --model)")
            mg.add_row(ui.mark(ui.INFO, "not set"), role, hint)
            continue
        mg.add_row(ui.mark(ui.OK, "ready"), role, f"{m} [{family(m)}]")
        if ping:
            try:
                r = llm.ping()
                cost = f", ${r.cost_usd:.5f}" if r.cost_usd is not None else ""
                mg.add_row("", "", ui.mark(ui.OK, f"answered ({r.input_tokens}+"
                                                  f"{r.output_tokens} tokens{cost})"))
            except LLMError as e:
                failed = True
                mg.add_row("", "", ui.mark(ui.FAIL, f"call failed: {e}"))
    c.print(mg)
    if s.models.second_opinion and same_family(s.models.judge, s.models.second_opinion):
        c.print(Text.assemble("  ", ui.mark(ui.WARN, "same maker"),
                              ": the judge and second opinion should come from different "
                              "model families"))
    if s.models.author:
        clash = [m for m in s.models.panel() if same_family(m, s.models.author)]
        if clash:
            c.print(Text.assemble("  ", ui.mark(ui.WARN, "left out"),
                                  f": {', '.join(clash)} share a family with the tasks' "
                                  f"author ({s.models.author}), so --deep-review won't use them"))

    if target:
        c.print()
        rc = runtime_plan(target)
        failed = failed or rc != 0

    c.print()
    if failed:
        c.print(Text.assemble(ui.mark(ui.FAIL, "Not ready"),
                              ": fix the lines marked fail above, then run rlgate doctor "
                              "again."))
    else:
        full = yr is None or dr is None
        c.print(Text.assemble(ui.mark(ui.OK, "Ready") if full else
                              ui.mark(ui.WARN, "Ready for quick checks"),
                              "" if full else ": full checks need Docker or Daytona"))
        c.print(Text.assemble("  Next: ", ("rlgate check ./task", "bold"),
                              ("   (or rlgate demo to see it on a broken example)", "dim")))
    hints = [] if ping else [("rlgate doctor --ping      check the keys work (one tiny real "
                             "call each)")]
    hints += ["rlgate doctor <folder>    how each task in a folder would run",
              "rlgate doctor --options   every Daytona setting ([environment] in rlgate.toml)"]
    for h in hints:
        c.print(Text(f"  {h}", style="dim"))
    return 1 if failed else 0


def _options(options_cls) -> int:
    from dataclasses import MISSING, fields

    c = ui.out()
    c.print(Text.assemble(("Daytona settings", "bold"),
                          ("  [environment] in rlgate.toml, or --backend-opt key=value", "dim")))
    for f in fields(options_cls):
        default = f.default if f.default is not MISSING else "-"
        c.print(Text.assemble(f"  {f.name:22}", (f"default {default!r}", "dim")))
    return 0


def runtime_plan(target: str) -> int:
    """How each task under ``target`` would run (no grading)."""
    from rlgate.cli.engine import CheckError, load_target
    from rlgate.runtime import precheck, probe_host

    c = ui.out()
    try:
        _, tasks = load_target(target)
    except CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    host = probe_host()
    plans = [precheck(t, host) for t in tasks]
    c.print(Text.assemble(("How each task would run", "bold"),
                          ((f"  {target} {ui.dot()} {len(plans)} task(s) {ui.dot()} nothing is "
                           "started"), "dim")))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    blocked = 0
    for p in plans:
        head = ui.mark(ui.OK, "full run") if p.usable else ui.mark(ui.WARN, "quick only")
        blocked += not p.usable
        body = Text.assemble((p.task_id, "bold"), "\n",
                             (f"{p.kind.value.replace('_', ' ')}: {p.spec.detail}", "dim"),
                             "\n", f"on {p.adapter}: {p.reason}")
        if not p.usable and p.remediation:
            body.append(f"\nto fix: {p.remediation}")
        grid.add_row(head, body)
    c.print(grid)
    if blocked:
        c.print(Text(f"  {blocked} task(s) can only get a quick check here; each says why "
                     "above.", style="dim"))
    return 0


def repro(*, rule: str, target: str, proof: str | None, show_patch: bool,
          cfg: S.Settings) -> int:
    from dataclasses import replace

    from rlgate.cli.engine import CheckError, load_target
    from rlgate.core import repro as R
    from rlgate.core import rules
    from rlgate.sandbox.select import BackendUnavailable, build_sandbox

    r = rules.get(rule)
    try:
        kind, tasks = load_target(target)
    except CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    if kind != "task":
        ui.error(f"{target} is a folder of tasks.", f"name one task: rlgate repro {rule} "
                                                   "<task-folder>")
        return 2
    task = tasks[0]
    recs = R.find(task.id, rule, proof)
    if not recs:
        ui.error(f"No recorded proof for {rule} on {task.id}.",
                 f"run a full check first: rlgate check {target}")
        return 2
    if len(recs) > 1:
        ui.error(f"{len(recs)} proofs are recorded for {rule} on {task.id}.",
                 "pick one with --proof:")
        for rec in recs:
            ui.echo(Text(f"    rlgate repro {rule} {target} --proof {rec.id}   ", "bold"),
                    Text(rec.note, "dim"), console=ui.err())
        return 2
    rec = recs[0]
    c = ui.out()
    c.print(Text.assemble((f"{r.id}  {r.title}", "bold")))
    c.print(Text.assemble(("  Proof:   ", "bold"), f"{rec.note}"))
    c.print(Text.assemble(("  Applies: ", "bold"),
                          "the reference solution script" if rec.oracle
                          else _patch_summary(rec.patch)))
    for cmd in rec.setup:
        c.print(Text.assemble(("  Setup:   ", "bold"), cmd))
    try:
        sandbox = build_sandbox(task, prefer=cfg.environment.type,
                                options=cfg.environment.kwargs)
        with ui.step("Build the environment (cached after the first run)"):
            sandbox.build()
        ws = getattr(sandbox, "workspace_dir", None)
        if ws is not None:
            task = replace(task, repo_dir=ws)
    except (BackendUnavailable, ValueError) as e:
        ui.error(str(e), "rlgate doctor shows what is set up")
        return 2
    try:
        with ui.step("Grade it with the task's own grader"):
            result = R.replay(task, rec, sandbox)
    finally:
        sandbox.close()
    g = result.grade
    c.print(Text.assemble(("  Grader:  ", "bold"), g.cmd))
    c.print(Text.assemble(("  Result:  ", "bold"),
                          f"exit {g.exit_code}, reward {g.reward} "
                          f"({'pass' if g.passed else 'fail'}; the problem shows as a "
                          f"{rec.expect})"))
    if show_patch and rec.patch:
        from rich.syntax import Syntax
        c.print(Syntax(rec.patch, "diff", theme="ansi_dark", background_color="default"))
    c.print()
    if result.reproduced(rec):
        c.print(Text.assemble(" ", ui.mark(ui.PROVEN, "reproduced"),
                              f": {r.title.lower()}. The problem is real and still there."))
        c.print(Text.assemble(" Next: ", (f"rlgate explain {rule} {target}", "bold"),
                              ("   what to change", "dim")))
        return 0
    c.print(Text.assemble(" ", ui.mark(ui.PASSED, "not reproduced"),
                          ": the grader no longer shows this problem (fixed, or it was "
                          "nondeterministic)."))
    c.print(Text.assemble(" Next: ", (f"rlgate check {target} --failed", "bold"),
                          ("   re-run what failed last time", "dim")))
    return 1


def _patch_summary(patch: str | None) -> str:
    if not patch:
        return "nothing (the unchanged workspace)"
    files = [ln[6:] for ln in patch.splitlines() if ln.startswith("+++ b/")]
    gone = [ln[6:] for ln in patch.splitlines() if ln.startswith("--- a/")]
    touched = sorted(set(files) | set(gone))
    shown = ", ".join(touched[:4]) + (f" (+{len(touched) - 4})" if len(touched) > 4 else "")
    return f"a patch to {shown}" if shown else "a patch"
