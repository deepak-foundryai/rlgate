"""The ``rlgate`` command line (Typer + Rich).

    rlgate check <task|folder>        the one command most users need
    rlgate explain [<rule> [<task>]]  every rule / one rule / one finding in full
    rlgate repro <rule> <task>        re-run the proof behind a proven finding
    rlgate waive <rule> <task>        accept a finding, with a recorded reason (--list, --remove)
    rlgate report | doctor | init | demo
    (hidden) rlgate clean | selftest DIR

Global flags work before or after the command: --verbose --quiet --no-color --ascii.
Old names (show, rules, waivers, cache clear, planted, --deep, --ek, --step, ...) still work
for one release, with a notice. Exit codes: 0 ready, 1 needs fixes, 2 broken or could not run.
"""

from __future__ import annotations

import difflib
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

try:                                    # typer >= 0.20 vendors click as typer._click
    from typer._click import exceptions as cx
except ImportError:                     # pragma: no cover - older typer uses click itself
    from click import exceptions as cx
from rich.table import Table
from rich.text import Text

from rlgate import __version__, ui
from rlgate import settings as S
from rlgate.config import load_dotenv
from rlgate.core import questions as Q
from rlgate.core import report as R
from rlgate.core import rules, store, waivers

app = typer.Typer(
    name="rlgate",
    help="Find reward-hacking holes in RL training environments, before you train on them.",
    rich_markup_mode="rich",
    add_completion=True,
    suggest_commands=True,
    no_args_is_help=False,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
ADVANCED = "Advanced"
# old command groups, kept one more release (hidden): rlgate clean / rlgate selftest
cache_app = typer.Typer(help="Old name of rlgate clean.", no_args_is_help=True)
app.add_typer(cache_app, name="cache", hidden=True)
planted_app = typer.Typer(help="Old name of rlgate selftest.", no_args_is_help=True)
app.add_typer(planted_app, name="planted", hidden=True)


class Fmt(str, Enum):
    terminal = "terminal"
    json = "json"
    markdown = "markdown"
    sarif = "sarif"
    html = "html"
    badge = "badge"


_EXT = {".svg": Fmt.badge, ".json": Fmt.json, ".md": Fmt.markdown, ".markdown": Fmt.markdown,
        ".sarif": Fmt.sarif, ".html": Fmt.html, ".htm": Fmt.html}


class _Globals:
    output: str | None = None
    jobs: int = 1


G = _Globals()


def _examples(*lines: str) -> str:
    return "[bold]Examples[/bold]\n\n" + "\n\n".join(f"  {ln}" for ln in lines)


# -- global flags (accepted anywhere on the line) -----------------------------------------

_FLAGS = {"--verbose": "verbose", "-v": "verbose", "--quiet": "quiet", "-q": "quiet",
          "--no-color": "no_color", "--ascii": "ascii"}
_VALUED = {"--output": "output", "-o": "output", "--jobs": "jobs", "-j": "jobs"}


def _take_globals(argv: list[str]) -> tuple[list[str], dict]:
    out: list[str] = []
    found: dict = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            out += argv[i:]
            break
        name, _, val = a.partition("=")
        if a in _FLAGS:
            found[_FLAGS[a]] = True
        elif name in _VALUED and (val or i + 1 < len(argv)):
            if not val:
                i += 1
                val = argv[i]
            found[_VALUED[name]] = val
        else:
            out.append(a)
        i += 1
    return out, found


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version, with Python "
                                          "and Docker versions for bug reports.",
                                          is_eager=True)] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show every check and "
                                          "every grading run.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No progress output.")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="No colour (also NO_COLOR=1; "
                                           "off automatically when piped).")] = False,
    ascii_: Annotated[bool, typer.Option("--ascii", help="ASCII symbols: \\[ok] \\[fail] "
                                         "\\[review] \\[warn].")] = False,
    output: Annotated[str | None, typer.Option("--output", "-o", hidden=True)] = None,
    jobs: Annotated[int, typer.Option("--jobs", "-j", hidden=True)] = 1,
) -> None:
    """Find reward-hacking holes in RL training environments, before you train on them."""
    if version:
        _version()
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        _start_here()
        raise typer.Exit(0)


def _start_here() -> None:
    c = ui.out()
    c.print(Text.assemble(("rlgate", "bold"), f" {__version__}: checks RL training "
                          "environments for reward-hacking holes, before you train on them."))
    c.print()
    c.print(Text("  Start here", style="bold"))
    for cmd, what in (("rlgate demo", "see real findings on a broken example (30 seconds)"),
                      ("rlgate check ./task", "check one task (a Harbor task folder)"),
                      ("rlgate check ./tasks", "check every task in a folder"),
                      ("rlgate doctor", "can this machine run full checks?")):
        c.print(Text.assemble(f"    {cmd:24}", (what, "dim")))
    c.print()
    c.print(Text("  rlgate --help lists every command.", style="dim"))


def _version() -> None:
    import platform
    import subprocess

    def docker() -> str:
        if shutil.which("docker") is None:
            return "not installed"
        try:
            r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, timeout=8, check=False)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            r = subprocess.run(["docker", "--version"], capture_output=True, text=True,
                               timeout=8, check=False)
            return (r.stdout.strip() or "installed") + " (daemon not running)"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    c = ui.out()
    c.print(f"rlgate {__version__}", highlight=False)
    c.print(f"Python {platform.python_version()} ({sys.executable})", highlight=False)
    c.print(f"Docker {docker()}", highlight=False)
    c.print(f"Platform {platform.platform()}", highlight=False)


# -- helpers --------------------------------------------------------------------------------

def _settings(config: str | None = None, backend: str | None = None,
              ek: list[str] | None = None, near: str | None = None) -> S.Settings:
    try:
        return S.load(config, backend=backend, env_kwargs=ek, near=near)
    except S.SettingsError as e:
        ui.error(str(e), "fix rlgate.toml (rlgate init writes a commented starter)")
        raise typer.Exit(2) from None


def _rule(rule: str) -> str:
    rid = rule.strip().upper()
    if rid in rules.RULES:
        return rid
    norm = rid[:2] + rid[2:].replace("O", "0").replace("I", "1").replace("L", "1")
    if norm in rules.RULES:
        close = [norm]
    else:
        close = difflib.get_close_matches(norm, list(rules.RULES), n=1, cutoff=0.6)
    hint = f" Did you mean {close[0]}?" if close else ""
    ui.error(f"There is no rule {rule!r}.{hint}", "rlgate explain lists every rule ID")
    raise typer.Exit(2)


def _fmt(fmt: Fmt | None) -> Fmt:
    if fmt is not None:
        return fmt
    if G.output:
        return _EXT.get(Path(G.output).suffix.lower(), Fmt.terminal)
    return Fmt.terminal


def _emit_text(text: str) -> None:
    if G.output:
        Path(G.output).write_text(text if text.endswith("\n") else text + "\n",
                                  encoding="utf-8")
        ui.echo(ui.mark(ui.OK, "wrote"), f" {G.output}", console=ui.err())
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _emit(views: list[R.TaskView], *, kind: str, target: str, fmt: Fmt, seconds: float = 0,
          mode: str = "full") -> None:
    from rlgate import render
    from rlgate.core.html import bank_html

    if fmt is Fmt.json:
        _emit_text(R.to_json(views[0]) if kind == "task" else
                   __import__("json").dumps(R.bank_dict(views, target), indent=2))
    elif fmt is Fmt.markdown:
        _emit_text(R.qa_card(views[0]) if kind == "task" else R.bank_markdown(views, target))
    elif fmt is Fmt.sarif:
        _emit_text(R.sarif(views))
    elif fmt is Fmt.badge:
        if not G.output:
            G.output = "rlgate-badge.svg"
        _emit_text(R.badge_svg(views))
    elif fmt is Fmt.html:
        if not G.output:
            G.output = "rlgate-report.html"
        _emit_text(bank_html(views, target))
    else:
        def draw() -> None:
            if kind == "task":
                render.task(views[0])
            else:
                render.bank(views, target, seconds, mode)
                if ui.verbose():
                    for v in views:
                        ui.out().print()
                        render.task(v)
        if G.output:
            from rich.console import Console
            with open(G.output, "w", encoding="utf-8") as fh:
                ui._S.out = Console(file=fh, no_color=True, width=100, highlight=False,
                                    emoji=False)
                try:
                    draw()
                finally:
                    ui._S.out = None
            ui.echo(ui.mark(ui.OK, "wrote"), f" {G.output}", console=ui.err())
        else:
            draw()


def _exit_for(views: list[R.TaskView]) -> int:
    return max((v.exit_code for v in views), default=2)


def _one_task(target: str):
    from rlgate.cli.engine import CheckError, load_target

    try:
        kind, tasks = load_target(target)
    except CheckError as e:
        ui.error(e.message, e.next_step)
        raise typer.Exit(2) from None
    if kind != "task":
        ui.error(f"{target} is a folder of {len(tasks)} tasks.", "name one task folder")
        raise typer.Exit(2)
    return tasks[0]


# -- check ----------------------------------------------------------------------------------

SCOPE, DEPTH, EXECUTION, OUTPUT, CONFIG = "Scope", "Depth", "Execution", "Output", "Config"


def _renamed(old: str, new: str) -> None:
    """An old flag or command still works for one release, with a notice."""
    ui.notice(f"{old} is now {new}; the old name stops working in the next release.")


def _layout(value: str) -> tuple[str | None, str | None, str | None]:
    """--layout NAME or FILE → (preset, mapping, adapter): a file is a mapping, a bundled
    name is a preset, anything else names an rlgate.adapters entry point."""
    from rlgate.ingest.presets import preset_names

    v = value.strip()
    if Path(v).expanduser().is_file():
        return None, str(Path(v).expanduser()), None
    if v.lower() in preset_names():
        return v.lower(), None, None
    return None, None, v


@app.command(epilog=_examples(
    "rlgate check ./rate-limiter              all six steps; a broken build or baseline "
    "stops the run",
    "rlgate check ./rate-limiter --quick      the file-only part of each step: seconds, no "
    "Docker",
    "rlgate check ./rate-limiter --only grader   one step (earlier results come from the "
    "last run)",
    "rlgate check ./tasks -j 4                every task in a folder, 4 at a time",
    "rlgate check ./task --deep-review --model openai/gpt-5 --model gemini/gemini-2.5-pro",
    "                                         add the LLM review with your own judge panel",
    "rlgate check ./tasks -o rlgate.sarif     SARIF annotations for GitHub"))
def check(
    target: Annotated[str, typer.Argument(help="A task folder, or a folder of tasks.")],
    # -- scope
    only: Annotated[list[str] | None, typer.Option(
        "--only", help="Run only these steps, comma-separated: build, baseline, leaks, "
        "grader, cheating, fairness (or 1-6). Also takes a rule ID, such as RG704.",
        rich_help_panel=SCOPE)] = None,
    start: Annotated[str | None, typer.Option(
        "--from", help="Start at this step (a name or 1-6); earlier steps come from the "
        "last run.", rich_help_panel=SCOPE)] = None,
    failed: Annotated[bool, typer.Option("--failed", help="Re-run only the checks that "
                                         "failed last time.", rich_help_panel=SCOPE)] = False,
    fresh: Annotated[bool, typer.Option("--fresh", help="Ignore saved results and check "
                                        "everything again.", rich_help_panel=SCOPE)] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Check at most this many tasks "
                                              "of a bank.", rich_help_panel=SCOPE)] = None,
    # -- depth
    quick: Annotated[bool, typer.Option("--quick", help="File-only checks: seconds, no "
                                        "Docker.", rich_help_panel=DEPTH)] = False,
    deep_review: Annotated[bool, typer.Option(
        "--deep-review", help="Add the LLM review: judges read the task, and an agent tries "
        "to cheat. Shows the cost and asks before spending.", rich_help_panel=DEPTH)] = False,
    model: Annotated[list[str] | None, typer.Option(
        "--model", help="With --deep-review: the judge model, any LiteLLM provider/model "
        "(anthropic/..., openai/..., gemini/..., ollama/...). Repeat it for a panel.",
        rich_help_panel=DEPTH)] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="With --deep-review: don't ask "
                                      "before spending. The budget in rlgate.toml still "
                                      "applies.", rich_help_panel=DEPTH)] = False,
    exhaustive: Annotated[bool, typer.Option(
        "--exhaustive", help="Grade every mutation instead of skipping variants that can't "
        "add a finding.", rich_help_panel=DEPTH)] = False,
    runs: Annotated[int | None, typer.Option("--runs", help="How many times to grade the "
                                             "reference and the do-nothing submission "
                                             "(default 5).", rich_help_panel=DEPTH)] = None,
    # -- execution
    jobs: Annotated[int | None, typer.Option("--jobs", "-j", help="Check this many tasks at "
                                             "once.", rich_help_panel=EXECUTION)] = None,
    backend: Annotated[str | None, typer.Option("--backend", help="Where full runs happen: "
                                                "auto, docker, daytona, local.",
                                                rich_help_panel=EXECUTION)] = None,
    backend_opt: Annotated[list[str] | None, typer.Option(
        "--backend-opt", help="One backend setting key=value, such as cpus=2 (rlgate doctor "
        "--options lists them).", rich_help_panel=EXECUTION)] = None,
    fail_fast: Annotated[bool, typer.Option(
        "--fail-fast", "-x", help="Stop at the first failing step. Without it, steps 3-6 all "
        "run; a broken build or baseline always stops the run.",
        rich_help_panel=EXECUTION)] = False,
    # -- output
    fmt: Annotated[Fmt | None, typer.Option("--format", "-f", help="Output format.",
                                            rich_help_panel=OUTPUT)] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Write the result to "
                                               "this file (format from the extension).",
                                               rich_help_panel=OUTPUT)] = None,
    # -- config
    config: Annotated[str | None, typer.Option("--config", help="Path to an rlgate.toml.",
                                               rich_help_panel=CONFIG)] = None,
    layout: Annotated[str | None, typer.Option(
        "--layout", help="Bank layout when it isn't detected: a preset name (harbor, "
        "swebench, deepswe, mimo), a mapping file, or an adapter. Also layout in "
        "rlgate.toml [checks].", rich_help_panel=CONFIG)] = None,
    # -- old names, accepted for one more release (hidden)
    deep: Annotated[bool, typer.Option("--deep", hidden=True)] = False,
    step: Annotated[int | None, typer.Option("--step", hidden=True)] = None,
    keep_going: Annotated[bool, typer.Option("--keep-going", hidden=True)] = False,
    parallel: Annotated[bool, typer.Option("--parallel", hidden=True)] = False,
    workers: Annotated[int | None, typer.Option("--workers", hidden=True)] = None,
    depth: Annotated[str | None, typer.Option("--depth", hidden=True)] = None,
    ek: Annotated[list[str] | None, typer.Option("--ek", hidden=True)] = None,
    preset: Annotated[str | None, typer.Option("--preset", hidden=True)] = None,
    mapping: Annotated[str | None, typer.Option("--mapping", hidden=True)] = None,
    adapter: Annotated[str | None, typer.Option("--adapter", hidden=True)] = None,
) -> int:
    """Check a task (or every task in a folder): is it OK, what is wrong, how to fix it."""
    from rlgate.cli import engine as E

    # -o / -j are also taken from anywhere on the line (main); the option form wins
    if output:
        G.output = output
    if jobs is not None:
        G.jobs = jobs
    if G.jobs < 1:
        ui.error("--jobs takes a number of at least 1.", "e.g. -j 4")
        return 2
    only = list(only or [])
    if deep:
        _renamed("--deep", "--deep-review")
        deep_review = True
    if step is not None:
        _renamed("--step N", "--only N")
        only.append(str(step))
    if keep_going:
        ui.notice("--keep-going is now the default and can be left out (-x stops at the "
                  "first failing step).")
    if ek:
        _renamed("--ek", "--backend-opt")
    opts = list(backend_opt or []) + list(ek or [])
    if depth is not None:
        _renamed("--depth", "--exhaustive (--depth fast is the default)")
        if depth not in S.DEPTHS:
            ui.error(f"--depth takes fast or full, got {depth!r}.", "use --exhaustive")
            return 2
        exhaustive = exhaustive or depth == "full"
    if workers is not None:
        ui.notice("--workers moves to rlgate.toml ([checks] workers = N); the flag stops "
                  "working in the next release.")
        if workers < 1:
            ui.error("--workers takes a number of at least 1.", "e.g. --workers 8")
            return 2
    for old, value in (("--preset", preset), ("--mapping", mapping), ("--adapter", adapter)):
        if value:
            _renamed(old, "--layout")
            layout = layout or value

    cfg = _settings(config, backend, opts, near=target)
    lay = layout or cfg.checks.layout
    source = E.Source(*(_layout(lay) if lay else (None, None, None)), limit)
    if model and not deep_review:
        ui.error("--model chooses the judges for the LLM review, which runs with "
                 "--deep-review.", f"rlgate check {target} --deep-review --model {model[0]}")
        return 2
    if not deep_review and any(n.strip().lower() == "questions" for g in only
                               for n in g.split(",")):
        ui.error("--only questions asks the LLM review's three questions, which run with "
                 "--deep-review.", f"rlgate check {target} --deep-review --only questions")
        return 2
    first = None
    if start is not None:
        try:
            first = Q.resolve_group(start).number
        except ValueError:
            ui.error(f"--from {start}: no such step.", "steps: build, baseline, leaks, "
                     "grader, cheating, fairness (or 1-6)")
            return 2
    review = None
    try:
        kind, tasks = E.load_target(target, source)
        # steps 3-6 at the same time: one task on a terminal (the live view shows them);
        # a bank already runs tasks side by side, so it stays one step at a time per task
        par = parallel or cfg.checks.parallel
        if par is None:
            par = kind == "task" and ui.err().is_terminal and not ui.quiet()
        speed = {"parallel": bool(par) and not fail_fast, "workers": workers,
                 "depth": "full" if exhaustive else None}
        go_on = not fail_fast
        if deep_review:
            review = _deep_review(cfg, model or [], tasks, yes=yes, quick=quick)
            if review is None:
                return 1
        only_ = only or None
        if kind == "bank":
            views, mode, secs = E.check_bank(
                tasks, target, quick=quick, cfg=cfg, runs=runs or 0, only_groups=only_,
                fresh=fresh, failed=failed, jobs=max(1, G.jobs), source=source,
                start=first, keep_going=go_on, review=review, **speed)
            _spent(review)
            _emit(views, kind="bank", target=target, fmt=_fmt(fmt), seconds=secs, mode=mode)
            return _exit_for(views)
        task = tasks[0]
        c = S.task_checks(cfg.checks, E.task_root(task), cfg.files)
        sel = E.select(only_, c.skip, start=first, keep_going=go_on,
                       parallel=speed["parallel"], workers=workers or c.workers,
                       depth=speed["depth"] or c.depth)
        sel.review = review
        mode = E.decide_mode(task, quick=quick, cfg=cfg)
        if mode.why_quick:
            ui.notice(mode.why_quick)
        k = runs or c.runs
        if mode.name == "full" and k < 3:
            ui.notice(f"{k} run(s) of the reference and do-nothing submissions can't show the "
                      "grade is consistent; the checklist asks for at least 3 (default 5).")
        view = E.check_task(task, target, mode=mode, runs=k, sel=sel,
                            fresh=fresh, failed=failed)
    except E.CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    store.set_last("task", target, [{"task": view.task_id, "target": target}])
    _spent(review)
    _emit([view], kind="task", target=target, fmt=_fmt(fmt))
    return view.exit_code


def _deep_review(cfg: S.Settings, models: list[str], tasks: list, *, yes: bool,
                 quick: bool = False):
    """--deep: the judge panel, checked and priced, after the user says yes. None when the
    user says no; CheckError when it can't run."""
    from rlgate.cli import engine as E
    from rlgate.llm import family
    from rlgate.review.session import Session

    cfg_source = cfg.source

    ms = cfg.models
    if models:
        ms.review = list(models)
    panel = ms.panel()
    if not panel:
        raise E.CheckError("--deep needs a judge model.", "add --model provider/model, or "
                           "[models] review in rlgate.toml")
    bad = [m for m in panel if "/" not in m]
    if bad:
        raise E.CheckError(f"model {bad[0]!r} has no provider prefix.",
                           f"use provider/model, e.g. --model anthropic/{bad[0]}")
    review = Session.from_settings(ms, models=panel)
    problems = review.problems()
    if problems:
        import os
        where = (f"settings: {cfg_source}" if cfg_source != "defaults" else
                 "no rlgate.toml found, so the default judge is used")
        nxt = "rlgate doctor shows the keys and models found; --model picks another"
        if os.environ.get("OPENROUTER_API_KEY") and not panel[0].startswith("openrouter/"):
            nxt = (f"your OpenRouter key works with --model openrouter/{family(panel[0])}/"
                   "<model>, or set [models] in ~/.config/rlgate/rlgate.toml "
                   "(rlgate init --user)")
        elif cfg_source == "defaults":
            nxt = ("rlgate init --user writes ~/.config/rlgate/rlgate.toml for every folder; "
                   "or pass --model provider/model")
        raise E.CheckError(f"--deep can't call its judge: {problems[0]} ({where}).", nxt)
    if ms.author and all(family(m) == family(ms.author) for m in panel):
        raise E.CheckError(
            f"every judge is from the {family(ms.author)} family, which wrote the tasks "
            f"([models] author = {ms.author}); judges must come from another family.",
            "add a judge from another family: --model provider/model")
    fams = {family(m) for m in panel}
    if len(fams) == 1 and ms.votes > 1:
        ui.notice(f"Every judge is from one model family ({fams.pop()}). Independent votes "
                  "work best from mixed families: add one with another --model.")
    est = review.estimate(tasks, agentic=not quick)
    cost = (f"about ${est.usd:.2f}" if est.usd is not None else
            "cost unknown (LiteLLM has no price for " + ", ".join(est.unpriced) + "; set "
            "one under [models.info.\"<model>\"] in rlgate.toml)")
    cap = (f" Stops at ${ms.budget_usd:.2f} ([models] budget_usd)." if ms.budget_usd
           else "")
    n = len(tasks)
    agent = (" On a full run that includes the L3 agent (at most "
             f"{ms.agent_turns} turns and {ms.agent_grades} gradings per audit, two audits "
             "per task), so it is an upper bound." if not quick else
             " The L3 agent needs a full run, so --quick leaves it out.")
    ui.notice(f"--deep: the LLM review makes about {est.calls} calls "
              f"({est.input_tokens:,} tokens in, about {est.output_tokens:,} out) "
              f"for {n} task{'s' if n != 1 else ''} with {', '.join(panel)}: {cost}. "
              f"A statement that needs a rewrite adds 2-3 calls.{agent}{cap} Review "
              "answers already in the cache cost nothing.")
    if yes:
        return review
    if not sys.stdin.isatty():
        raise E.CheckError("--deep asks before spending money, and there is no terminal to "
                           "ask on.", "add --yes to run the LLM review without asking")
    if not typer.confirm("Run the LLM review?", default=False, err=True):
        ui.echo("Nothing was run.", console=ui.err())
        return None
    return review


def _spent(review) -> None:
    if review is None:
        return
    cached = f", {review.cached} from the cache" if review.cached else ""
    ui.err().print(f"LLM review: {review.calls} call{'s' if review.calls != 1 else ''}"
                   f"{cached}, ${review.spent:.2f} spent.", style="dim", highlight=False)


# -- explain / repro -------------------------------------------------------------------------

@app.command(epilog=_examples("rlgate explain                         every rule ID with its title",
                              "rlgate explain RG201                   what the rule means, with "
                              "a bad and a good example",
                              "rlgate explain RG201 ./rate-limiter    that finding in full: What, "
                              "Why, Proof, Fix"))
def explain(
    rule: Annotated[str | None, typer.Argument(help="Rule ID, e.g. RG201. Leave out to list "
                                               "every rule.")] = None,
    target: Annotated[str | None, typer.Argument(help="A task folder: show that finding "
                                                 "in full.")] = None,
    group: Annotated[str | None, typer.Option("--step", hidden=True)] = None,
) -> int:
    """Explain a rule, or a finding on a task; with nothing, list every rule."""
    from rlgate import render

    if rule is None:
        return _rules(group)
    rid = _rule(rule)
    if target is not None:
        return _finding(rid, target)
    render.explain(rid)
    return 0


def _rules(group: str | None) -> int:
    from rlgate import render

    only_step = None
    if group:
        try:
            only_step = Q.resolve_group(group)
        except ValueError as e:
            ui.error(str(e).capitalize(), "rlgate explain")
            return 2
    render.rules_table(only_step)
    return 0


def _finding(rid: str, target: str) -> int:
    from rlgate import render
    from rlgate.cli import engine as E

    task = _one_task(target)
    try:
        view = E.saved_view(task, target)
    except E.CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    if view is None:
        ui.error(f"No result for {task.id} yet.", f"rlgate check {target}")
        return 2
    found = store.latest_for(task.id)
    if found and E.changed_since(view, found[1].get("fingerprint")):
        ui.notice(f"{target} changed since this result. rlgate check {target} refreshes it.")
    return render.show(view, rid)


@app.command("show", hidden=True)
def show(rule: Annotated[str, typer.Argument()], target: Annotated[str, typer.Argument()]
         ) -> int:
    """Old name of rlgate explain <rule> <task>."""
    _renamed("rlgate show <rule> <task>", "rlgate explain <rule> <task>")
    return _finding(_rule(rule), target)


@app.command("rules", hidden=True)
def rules_cmd(group: Annotated[str | None, typer.Option("--step", "--group")] = None) -> int:
    """Old name of rlgate explain (no arguments)."""
    _renamed("rlgate rules", "rlgate explain")
    return _rules(group)


@app.command(rich_help_panel=ADVANCED, epilog=_examples("rlgate repro RG201 ./rate-limiter",
                              "rlgate repro RG101 ./rate-limiter --show-patch"))
def repro(
    rule: Annotated[str, typer.Argument(help="Rule ID of a proven finding.")],
    target: Annotated[str, typer.Argument(help="The task folder.")],
    proof: Annotated[str | None, typer.Option("--proof", help="Proof ID, when the rule has "
                                              "several.")] = None,
    show_patch: Annotated[bool, typer.Option("--show-patch", help="Print the exact patch "
                                             "the proof applies.")] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="Where it runs: auto, "
                                                "docker, daytona, local.")] = None,
    backend_opt: Annotated[list[str] | None, typer.Option(
        "--backend-opt", help="One backend setting key=value.")] = None,
    ek: Annotated[list[str] | None, typer.Option("--ek", hidden=True)] = None,
) -> int:
    """Re-run the proof for a proven finding, so you can watch it happen."""
    from rlgate.cli.doctor import repro as run

    if ek:
        _renamed("--ek", "--backend-opt")
    return run(rule=_rule(rule), target=target, proof=proof, show_patch=show_patch,
               cfg=_settings(None, backend, list(backend_opt or []) + list(ek or []),
                             near=target))


# -- waivers ---------------------------------------------------------------------------------

@app.command(epilog=_examples(
    'rlgate waive RG403 ./rate-limiter -r "the exact message is part of the spec"',
    "rlgate waive RG604 ./rate-limiter            (asks for the reason)",
    "rlgate waive --list ./tasks                  current waivers; stale ones flagged",
    "rlgate waive --remove RG403 ./rate-limiter   delete a waiver"))
def waive(
    rule: Annotated[str | None, typer.Argument(help="Rule ID of the finding to accept.")
                    ] = None,
    target: Annotated[str | None, typer.Argument(help="The task folder.")] = None,
    reason: Annotated[str | None, typer.Option("--reason", "-r", help="Why this is "
                                               "acceptable (required).")] = None,
    author: Annotated[str | None, typer.Option("--author", help="Defaults to git config "
                                               "user.name.")] = None,
    list_: Annotated[bool, typer.Option("--list", help="List current waivers of a task or "
                                        "folder (default: the last check) and flag stale "
                                        "ones.")] = False,
    remove: Annotated[bool, typer.Option("--remove", help="Delete the waiver of <rule> on "
                                         "<task>.")] = False,
) -> int:
    """Accept a finding instead of fixing it, with a recorded reason (in rlgate.toml)."""
    from rlgate.cli import engine as E

    if list_:
        if target is not None:
            ui.error("--list takes one task or folder.", "rlgate waive --list ./tasks")
            return 2
        return _list_waivers(rule)          # the one argument is the task or folder
    if rule is None or target is None:
        ui.error("waive needs a rule and a task.", 'rlgate waive RG403 ./task -r "why"; '
                 "rlgate waive --list shows current waivers")
        return 2
    rid = _rule(rule)
    task = _one_task(target)
    if remove:
        n = waivers.remove(E.task_root(task), rid)
        if not n:
            ui.error(f"{rid} has no waiver on {task.id}.", f"rlgate waive --list {target}")
            return 2
        ui.echo(ui.mark(ui.OK, "removed"), f" the waiver of {rid} on {task.id}; the finding "
                "is open again")
        ui.echo(Text.assemble(" Next: ", (f"rlgate check {target}", "bold")))
        return 0
    view = E.saved_view(task, target)
    if view is None:
        ui.error(f"No result for {task.id} yet.", f"rlgate check {target}")
        return 2
    open_ = [f for f in view.findings if f.rule_id == rid and not f.waiver]
    if not open_:
        ui.error(f"{rid} isn't an open finding on {task.id} in the last check.",
                 f"rlgate check {target} lists the open findings")
        return 2
    if not reason and sys.stdin.isatty():
        reason = typer.prompt(f"Why is {rid} acceptable for this task?")
    if not reason:
        ui.error("A waiver needs a reason.", f'rlgate waive {rid} {target} -r "..."')
        return 2
    try:
        w = waivers.add(view.root, open_[0], reason, author)
    except waivers.WaiverError as e:
        ui.error(str(e), f"rlgate explain {rid} {target} says how to fix it"
                 if "only be fixed" in str(e) else None)
        return 2
    c = ui.out()
    c.print(Text.assemble(ui.mark(ui.ACCEPTED, "accepted, with reason"),
                          f": {rid} on {task.id}"))
    c.print(Text(f"  Recorded in {Path(target) / waivers.FILE} by {w.author} on {w.date}. "
                 "It shows on every report"
                 + (f", and is re-raised if {', '.join(w.files)} changes." if w.files
                    else "."), style="dim"), overflow="fold")
    c.print(Text.assemble(" Next: ", (f"rlgate check {target}", "bold")))
    return 0


@app.command("waivers", hidden=True)
def list_waivers(target: Annotated[str | None, typer.Argument()] = None) -> int:
    """Old name of rlgate waive --list."""
    _renamed("rlgate waivers", "rlgate waive --list")
    return _list_waivers(target)


def _list_waivers(target: str | None) -> int:
    from rlgate.cli import engine as E

    roots: list[tuple[str, Path]] = []
    if target is None:
        last = store.get_last()
        if not last:
            ui.error("Nothing checked yet.", "rlgate waive --list ./task, or rlgate check ./task "
                     "first")
            return 2
        target = str(last["target"])
    try:
        _, tasks = E.load_target(target)
    except E.CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    for t in tasks:
        roots.append((t.id, E.task_root(t)))
    table = Table(box=ui.box(), show_edge=False, pad_edge=False, header_style="bold")
    for col in ("Task", "Rule", "Status", "Reason", "By"):
        table.add_column(col, overflow="fold")
    n = stale_n = 0
    for tid, root in roots:
        try:
            ws = waivers.load(root)
        except waivers.WaiverError as e:
            ui.error(str(e))
            return 2
        for w in ws:
            changed = waivers.is_stale(w, root)
            n += 1
            stale_n += bool(changed)
            status = (ui.mark(ui.WARNING, "re-raised: " + ", ".join(changed) + " changed")
                      if changed else ui.mark(ui.ACCEPTED, "accepted"))
            table.add_row(tid, w.rule, status, w.reason, f"{w.author}, {w.date}")
    c = ui.out()
    if not n:
        c.print(Text(f"No waivers for {target}.", style="dim"))
        return 0
    c.print(table)
    if stale_n:
        c.print(Text(f"\n {stale_n} waiver(s) no longer apply because their files changed; "
                     "the findings are open again.", style="dim"))
    return 0


# -- demo ------------------------------------------------------------------------------------

_DEMO = Path(__file__).resolve().parent.parent / "demo" / "rate-limiter"
_DEMO_NAME = "rlgate/demo-rate-limiter"


@app.command(rich_help_panel=ADVANCED, epilog=_examples("rlgate demo", "rlgate demo --full     also grade it (needs "
                                             "Docker or Daytona)"))
def demo(
    full: Annotated[bool, typer.Option("--full", help="A full run: builds and grades the "
                                       "task (needs Docker or Daytona).")] = False,
    into: Annotated[str, typer.Option("--dir", help="Where to put the demo task.")
                    ] = "rate-limiter",
) -> int:
    """Check a bundled, deliberately broken Harbor task to see real findings."""
    from rlgate.cli import engine as E

    dest = Path(into)
    if dest.exists() and _DEMO_NAME not in _read(dest / "task.toml"):
        ui.error(f"{dest} already exists and isn't the rlgate demo.",
                 "rlgate demo --dir <another-folder>")
        return 2
    if not dest.exists():
        shutil.copytree(_DEMO, dest)
        ui.echo(Text(f"Copied a deliberately broken Harbor task to ./{dest.as_posix()}: open "
                     "it, fix what rlgate finds, and check it again.", style="dim"),
                console=ui.err())
    target = "./" + dest.as_posix() if not dest.is_absolute() else str(dest)
    cfg = _settings()
    task = _one_task(target)
    try:
        mode = E.decide_mode(task, quick=not full, cfg=cfg)
        if mode.why_quick:
            ui.notice(mode.why_quick)
        view = E.check_task(task, target, mode=mode, runs=cfg.checks.runs, sel=E.Selection(),
                            fresh=True)
    except E.CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    if view.outcome is not None and not full:
        for row in view.outcome.rows:
            row.summary = row.summary.replace("run without --quick", "rlgate demo --full") \
                .replace("the rest needs a full run", "the rest needs rlgate demo --full")
    store.set_last("task", target, [{"task": view.task_id, "target": target}])
    _emit([view], kind="task", target=target, fmt=_fmt(None))
    return view.exit_code


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


# -- report / rules / doctor / init / cache --------------------------------------------------

@app.command(rich_help_panel=ADVANCED, epilog=_examples("rlgate report --format html -o report.html",
                              "rlgate report --format markdown      the QA card",
                              "rlgate report -o rlgate-badge.svg    a badge for the README"))
def report(
    fmt: Annotated[Fmt | None, typer.Option("--format", "-f", help="Output format.")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Write the result to "
                                               "this file (format from the extension).")
                      ] = None,
) -> int:
    """Re-render the last result (HTML, Markdown, JSON, SARIF, badge) without re-running."""
    from rlgate.cli import engine as E

    if output:
        G.output = output

    last = store.get_last()
    if not last:
        ui.error("Nothing checked yet.", "rlgate check ./task")
        return 2
    target = last["target"]
    try:
        _kind, tasks = E.load_target(target, E.Source(**last.get("source", {})))
    except E.CheckError as e:
        ui.error(e.message, e.next_step)
        return 2
    by_id = {e["task"]: e.get("target", target) for e in last.get("entries", [])}
    views = []
    for t in tasks:
        if t.id not in by_id:
            continue
        v = E.saved_view(t, by_id[t.id])
        if v is not None:
            views.append(v)
    if not views:
        ui.error("The saved results are gone (rlgate clean?).", f"rlgate check {target}")
        return 2
    mode = views[0].mode
    _emit(views, kind="task" if last["kind"] == "task" else "bank", target=target,
          fmt=_fmt(fmt), mode=mode)
    return _exit_for(views)


@app.command(rich_help_panel=ADVANCED, epilog=_examples("rlgate doctor", "rlgate doctor ./tasks      how each task "
                                               "would run", "rlgate doctor --ping"))
def doctor(
    target: Annotated[str | None, typer.Argument(help="Optional: a task or folder, to see "
                                                 "how each task would run.")] = None,
    ping: Annotated[bool, typer.Option("--ping", help="Make one tiny real call per key.")
                    ] = False,
    options: Annotated[bool, typer.Option("--options", help="List every Daytona setting.")
                       ] = False,
    model: Annotated[str | None, typer.Option("--model", help="Judge model to check.")
                     ] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> int:
    """Can this machine run full checks, and what is missing."""
    from rlgate.cli.doctor import doctor as run

    return run(config=config, model=model, ping=ping, options=options, target=target)


_STARTER = '''# rlgate settings for this task. Docs: rlgate --help, rlgate rules, rlgate explain <rule>

[checks]
# Steps to leave out, by name: build, baseline, leaks, grader, cheating, fairness
# (or single rule IDs, such as "RG604").
skip = []

[thresholds]
# How many times a full check grades the reference solution and a do-nothing submission.
runs = 5

# Waivers: a recorded decision to accept a finding instead of fixing it. Add one with
#   rlgate waive <rule> <task> --reason "..."
# Each shows on every report as "accepted, with reason" and is re-raised when the files it
# refers to change. Proven findings of high severity can't be waived, only fixed.
#
# [[waivers]]
# rule = "RG403"
# reason = "The exact message is part of the spec"
# author = "Your Name"
# date = "2026-11-10"
'''


_USER_STARTER = '''# Your rlgate settings, used from every folder. A project's or bank's own rlgate.toml
# (found from where you run, or from the task you check) overrides these key by key;
# --config, RLGATE_* variables and flags override both. Keys go in .env beside this file.

[environment]
# Where full checks run: auto, local, docker or daytona.
type = "auto"

[models]
# Only rlgate check --deep uses models. Any LiteLLM provider/model, with that provider's
# key in .env (ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, ...).
# review = ["openrouter/<maker>/<model>", "openrouter/<other-maker>/<model>"]
# agent = "openrouter/<maker>/<model>"     # the L3 agent (empty: the first review judge)
# budget_usd = 5.0                          # stop the LLM work at this spend per run
#
# A model LiteLLM has no price for (common behind OpenRouter): its price in $ per token.
# [models.info."openrouter/<maker>/<model>"]
# input_cost_per_token = 0.0000005
# output_cost_per_token = 0.0000015
'''


@app.command(rich_help_panel=ADVANCED, epilog=_examples("rlgate init ./rate-limiter", "rlgate init      (this folder)", "rlgate init --user   (your settings, for every folder)"))
def init(
    target: Annotated[str, typer.Argument(help="The task folder (default: here).")] = ".",
    user: Annotated[bool, typer.Option("--user", help="Write your own settings instead "
                                       "(~/.config/rlgate/rlgate.toml): models and backend "
                                       "for every folder you run rlgate in.")] = False,
) -> int:
    """Create a starter rlgate.toml (checks to skip, thresholds, waivers)."""
    if user:
        d = S.user_dir()
        path = d / S.CONFIG_NAME
        if path.exists():
            ui.error(f"{path} already exists.", "edit it, or delete it and run rlgate init "
                                                "--user again")
            return 2
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(_USER_STARTER)
        ui.echo(ui.mark(ui.OK, "created"), f" {path}")
        ui.echo(Text(f"Put provider keys in {d / '.env'} (never in rlgate.toml), then run "
                     "rlgate doctor.", style="dim"))
        return 0
    path = Path(target) / S.CONFIG_NAME
    if path.exists():
        ui.error(f"{path} already exists.", f"edit it, or delete it and run rlgate init "
                                            f"{target} again")
        return 2
    if not Path(target).is_dir():
        ui.error(f"{target} isn't a folder.", "rlgate init <task-folder>")
        return 2
    path.write_text(_STARTER)
    ui.echo(ui.mark(ui.OK, "created"), f" {path}")
    return 0


# -- clean / selftest (hidden: rlgate itself, not your tasks) ------------------------------

@app.command("clean", hidden=True, epilog=_examples("rlgate clean"))
def clean() -> int:
    """Delete saved results, so every task is checked again (proofs for repro are kept)."""
    n = store.clear()
    ui.echo(ui.mark(ui.OK, "cleared"), f" {n} saved result file(s) "
            f"{ui.dash()} the next check of each task runs from scratch")
    return 0


@cache_app.command("clear")
def cache_clear() -> int:
    """Old name of rlgate clean."""
    _renamed("rlgate cache clear", "rlgate clean")
    return clean()


@app.command("selftest", hidden=True, epilog=_examples(
    "rlgate selftest ./planted                      file-only and execution checks",
    "rlgate selftest ./planted --deep-review -y     with the LLM review (spends money)"))
def selftest(
    out: Annotated[Path, typer.Argument(help="Folder to write the planted bank to.")],
    deep_review: Annotated[bool, typer.Option("--deep-review", help="Include the LLM review "
                                              "(most planted defects need it).")] = False,
    model: Annotated[list[str] | None, typer.Option("--model", help="Judge model(s).")
                     ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask before spending.")
                   ] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="auto, docker, daytona, "
                                                "local.")] = None,
) -> int:
    """Test rlgate itself: write tasks with one planted defect each (and a clean one), check
    them, and score what was found and what fired where it shouldn't."""
    from rlgate import planted

    for d in planted.build(out):
        ui.echo(ui.mark(ui.OK, "wrote"), f" {d}", console=ui.err())
    check(str(out), deep_review=deep_review, model=model, yes=yes, backend=backend,
          fresh=True)
    ui.out().print()
    return _score(out)          # a case that couldn't run shows as "not checked yet"


@planted_app.command("build")
def planted_build(out: Annotated[Path, typer.Argument()]) -> int:
    """Old: rlgate selftest builds, checks and scores in one pass."""
    from rlgate import planted

    _renamed("rlgate planted build / score", "rlgate selftest DIR")
    for d in planted.build(out):
        ui.echo(ui.mark(ui.OK, "wrote"), f" {d}")
    return 0


@planted_app.command("score")
def planted_score(bank: Annotated[Path, typer.Argument()]) -> int:
    """Old: rlgate selftest builds, checks and scores in one pass."""
    _renamed("rlgate planted build / score", "rlgate selftest DIR")
    return _score(bank)


def _score(bank: Path) -> int:
    """Score the saved results of a planted bank: did each planted defect get found, and
    did anything fire on a task where it shouldn't?"""
    import json

    from rlgate import planted
    try:
        labels = json.loads((bank / "labels.json").read_text())
    except (OSError, ValueError):
        ui.error(f"{bank}/labels.json not found", f"rlgate selftest {bank}")
        return 2
    t = Table(title="Planted defects", show_lines=False)
    for col in ("Case", "Planted", "Found it", "False alarms", "Rules reported"):
        t.add_column(col)
    scores = []
    for name, label in labels.items():
        saved = store.latest_for(f"planted-{name}")
        if saved is None:
            sc = planted.Score(name, None, [], [], skipped="not checked yet")
        else:
            sc = planted.score_one(name, label, {f.rule_id for f in saved[0].findings})
        scores.append(sc)
        found = ("not checked yet" if sc.skipped else
                 "(nothing expected)" if sc.hit is None else "yes" if sc.hit else "NO")
        t.add_row(name, label["what"], found, ", ".join(sc.false_alarms) or "none",
                  ", ".join(sc.found) or "none")
    ui.out().print(t)
    s = planted.summary(scores)
    recall = "n/a" if s["recall"] is None else f"{s['recall']:.0%}"
    ui.echo(f"{s['ran']} of {s['cases']} cases checked; planted defects found: {recall}; "
            f"cases with a false alarm: {s['false_alarm_cases']}")
    return 0 if s["false_alarm_cases"] == 0 and s["recall"] in (None, 1.0) else 1


# -- entry point -------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    args, flags = _take_globals(args)
    ui.configure(no_color=bool(flags.get("no_color")), ascii=bool(flags.get("ascii")),
                 quiet=bool(flags.get("quiet")), verbose=bool(flags.get("verbose")))
    G.output = flags.get("output")
    try:
        G.jobs = int(flags.get("jobs", 1))
    except ValueError:
        ui.error(f"--jobs takes a number, got {flags['jobs']!r}.", "e.g. --jobs 4")
        return 2
    load_dotenv()
    try:
        rv = app(args=args, prog_name="rlgate", standalone_mode=False)
    except cx.NoSuchOption as e:
        ui.error(e.format_message(), f"rlgate {_cmd(args)}--help shows the options")
        return 2
    except cx.UsageError as e:
        ui.error(e.format_message(), f"rlgate {_cmd(args)}--help")
        return 2
    except typer.Abort:
        ui.echo("", console=ui.err())
        return 130
    except typer.Exit as e:
        return e.exit_code
    except BrokenPipeError:
        return _quiet_pipe()
    except SystemExit as e:             # Rich exits 1 on a closed pipe (`| head`)
        if isinstance(e.__context__, BrokenPipeError):
            return _quiet_pipe()
        raise
    except KeyboardInterrupt:
        ui.error("Interrupted.", "run the same command again; finished tasks are cached")
        return 130
    return rv if isinstance(rv, int) else 0


def _quiet_pipe() -> int:
    """The reader closed the pipe (``rlgate check . | head``): stop quietly."""
    import os
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:
        pass
    return 0


def _cmd(args: list[str]) -> str:
    names = {"check", "explain", "repro", "waive", "demo", "report", "doctor", "init",
             "clean", "selftest", "show", "rules", "waivers", "cache", "planted"}
    first = next((a for a in args if not a.startswith("-")), None)
    return f"{first} " if first in names else ""


if __name__ == "__main__":
    raise SystemExit(main())
