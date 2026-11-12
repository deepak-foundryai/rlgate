"""What ``rlgate check`` does, shared by check / demo / show / waive / report.

    target ─► a task or a bank ─► quick or full ─► which checks (--only, --failed, [checks])
           ─► cached result or a fresh run ─► waivers applied ─► TaskView (core/report.py)
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from rlgate import settings as S
from rlgate import ui
from rlgate.core import questions as Q
from rlgate.core import rules, store, waivers
from rlgate.core.report import TaskView
from rlgate.core.rules import STEPS, Step
from rlgate.core.task import Capability, Task
from rlgate.sandbox.errors import is_infrastructure

NONE = "__no_probes__"          # `--only works`: the baseline only, no probes


class CheckError(Exception):
    """Could not check: ``message`` says what happened, ``next_step`` what to do."""

    def __init__(self, message: str, next_step: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.next_step = next_step


# -- targets ------------------------------------------------------------------------------

@dataclass
class Source:
    """How to read a bank that isn't auto-detected (uncommon: the common case needs none)."""
    preset: str | None = None
    mapping: str | None = None
    adapter: str | None = None
    limit: int | None = None

    def given(self) -> bool:
        return bool(self.preset or self.mapping or self.adapter or self.limit)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def load_target(target: str, source: Source | None = None) -> tuple[str, list[Task]]:
    """("task", [task]) or ("bank", tasks)."""
    from rlgate.ingest import harbor, spec
    from rlgate.ingest.bank import detect_layout, load_bank

    source = source or Source()
    p = Path(target)
    if not p.exists():
        raise CheckError(f"{target} doesn't exist.",
                         "point at a task folder (instruction.md, environment/, tests/) or a "
                         "folder of tasks")
    single = not source.given() and (
        harbor.is_task_dir(p) or (p / "task.rlgate.yaml").is_file()
        or (p.is_file() and p.suffix not in (".jsonl", ".json")))
    if single:
        try:
            return "task", [spec.load(p)]
        except Exception as e:
            raise CheckError(f"Couldn't read the task at {target}: {e}",
                             "a Harbor task needs instruction.md, task.toml, environment/ "
                             "and tests/") from e
    preset = source.preset
    if not (preset or source.mapping or source.adapter):
        preset = detect_layout(p)
    try:
        tasks = load_bank(p, preset=preset, mapping=source.mapping, adapter=source.adapter,
                          limit=source.limit)
    except Exception as e:
        raise CheckError(f"Couldn't read tasks in {target}: {e}",
                         "point at one task folder, or a folder whose subfolders are tasks "
                         "(other layouts: --layout)") from e
    if not tasks:
        raise CheckError(f"No tasks found in {target}.",
                         "a task folder has instruction.md, task.toml, environment/ and "
                         "tests/; a bank is a folder of those (other layouts: "
                         "--layout)")
    return "bank", tasks


def task_root(task: Task) -> Path:
    if task.origin is None and task.source == "swebench" and task.meta.get("config_file"):
        # a row of a JSONL bank: its file is what the user points at (its workspace is in
        # an image; repo_dir is only a stand-in)
        return Path(task.meta["config_file"])
    return waivers.task_root(task.origin or task.repo_dir)


def task_target(task: Task, bank_target: str | None = None) -> str:
    """How to refer to a task in commands: the path the user would type."""
    if bank_target is None:
        return str(task.origin or task.repo_dir)
    root = task_root(task)
    try:
        return "./" + root.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(root)


# -- mode ---------------------------------------------------------------------------------

@dataclass
class Mode:
    name: str                       # "quick" | "full"
    backend: str = "auto"
    options: dict | None = None
    full_possible: bool = True
    why_quick: str | None = None    # set when we fell back to quick
    limit: str | None = None        # rlgate can't run this task fully (shown on rows)
    missing: str | None = None      # what this machine lacks for a full run (step 1 says it)


def decide_mode(task: Task, *, quick: bool, cfg: S.Settings) -> Mode:
    from rlgate.sandbox.select import resolve_backend

    backend, options = cfg.environment.type, cfg.environment.kwargs
    limits = task.meta.get("unsupported") if isinstance(task.meta, dict) else None
    if limits:
        why = _limit_text(task)
        return Mode("quick", backend, options, full_possible=False, limit=why,
                    why_quick=None if quick else
                    f"{task.id}: a full run isn't possible ({why}), so this is a quick run.")
    choice = resolve_backend(task, backend)
    container = Capability.CONTAINER in task.capabilities or task.staged
    possible = choice.usable and not (container and choice.name == "local")
    if quick:
        return Mode("quick", backend, options, full_possible=possible)
    if backend != "auto" and not choice.usable:
        raise CheckError(f"{_backend_name(backend)} isn't available: {choice.unavailable}",
                         "fix that, or run with --quick for the file-only checks "
                         "(rlgate doctor shows what is set up)")
    if not possible:
        return Mode("quick", backend, options, full_possible=False,
                    missing="Docker isn't running and Daytona isn't set up",
                    why_quick="Docker isn't running and Daytona isn't set up, so this is a "
                              "quick run (file-only checks). Start Docker or set "
                              "DAYTONA_API_KEY for a full run; rlgate doctor shows what's "
                              "missing.")
    return Mode("full", choice.name, options)


def _limit_text(task: Task) -> str:
    issues = set(task.meta.get("format_issues", []) or [])
    from rlgate.ingest.harbor import _sentence

    rlgate_side = [u for u in task.meta["unsupported"] if _sentence(u) not in issues]
    if rlgate_side:
        return "; ".join(u.split(" — ")[0] for u in rlgate_side)
    return "the task files have problems (see RG605)"


def _backend_name(b: str) -> str:
    return {"docker": "Docker", "daytona": "Daytona", "local": "Local"}.get(b, b)


# -- which checks -------------------------------------------------------------------------

@dataclass
class Selection:
    only: set[str] | None = None            # probe ids (None = all)
    selected: set[Step] | None = None       # steps asked for (None = all six)
    skipped: set[Step] | None = None        # left out by [checks] skip
    steps: set[int] | None = None           # steps to run (None = all, in order)
    keep_going: bool = False                # run steps 3-6 even after a failure
    parallel: bool = False                  # steps 3-6 at the same time, results at the end
    workers: int = 1                        # grading runs at a time inside the task
    depth: str = "fast"                     # fast | full: which variants are graded
    review: Any = None                      # --deep-review: the LLM review session

    @property
    def deep(self) -> list[str] | None:
        """The review models when --deep-review is on (part of the cache key)."""
        return list(self.review.models) if self.review is not None else None


def _needs_baseline(sel: Selection) -> bool:
    """Whether the selected checks grade anything. False only for a run of checks that
    just inspect the environment (e.g. --only leaks), as the pipeline decides."""
    from rlgate.core.registry import all_probes
    from rlgate.core.rules import step_of_probe

    if sel.steps is None or sel.steps & {1, 2}:
        return True
    for pid, cls in all_probes().items():
        st = step_of_probe(pid)
        if st is None or st.number not in sel.steps:
            continue
        if (sel.only is None or pid in sel.only) and cls.meta.needs_baseline:
            return True
    return False


def select(only_groups: list[str] | None, skip: list[str], *, step: int | None = None,
           start: int | None = None, keep_going: bool = False, parallel: bool = False,
           workers: int = 1, depth: str = "fast") -> Selection:
    """--only <step names or 1-6>, --from N and rlgate.toml [checks] skip → what runs."""
    from rlgate.core.registry import discover

    all_probes = set(discover())
    names = [n.strip() for g in only_groups or [] for n in g.split(",") if n.strip()]
    picked: set[str] = set()          # single checks: "questions", or an exact rule ID
    steps_named = []
    for n in names:
        if n.lower() in Q.CHECK_SETS:
            picked |= Q.CHECK_SETS[n.lower()]
        elif re.fullmatch(r"RG\d{3}", n.upper()) and n.upper() in rules.RULES:
            picked.add(rules.get(n.upper()).probe_id)
        else:
            steps_named.append(n)
    try:
        groups = {Q.resolve_group(g) for g in steps_named}
    except ValueError as e:
        raise CheckError(str(e).capitalize(), "rlgate explain lists the steps; --only also "
                         "takes a rule ID (RG704) or questions") from e
    if picked:
        groups |= {s for s in (Q.step_of_probe(p) for p in picked) if s is not None}
    for n, flag in ((step, "--step"), (start, "--from")):
        if n is not None and not 1 <= n <= len(STEPS):
            raise CheckError(f"{flag} {n}: steps are numbered 1 to {len(STEPS)}",
                             "rlgate check --help lists them")
    if step is not None:
        groups |= {Step(step)}
    if start is not None:
        groups |= {s for s in STEPS if s.number >= start}
    skip_groups: set[Step] = set()
    skip_rules: set[str] = set()
    for name in skip:
        if name.upper() in rules.RULES:
            skip_rules.add(name.upper())
            continue
        try:
            skip_groups.add(Q.resolve_group(name))
        except ValueError as e:
            raise CheckError(f"rlgate.toml [checks] skip: {e}", "rlgate explain lists the "
                             "steps and rule IDs") from e

    probes = set(all_probes)
    if picked:                        # the named checks, plus every check of a named step
        named = {Q.resolve_group(g) for g in steps_named}
        probes = picked | set().union(set(), *(Q.probes_in(g, all_probes) for g in named))
    probes -= set().union(set(), *(Q.probes_in(g, all_probes) for g in skip_groups))
    probes -= {p for p in probes
               if rules.for_probe(p) and all(r.id in skip_rules for r in rules.for_probe(p))}
    selected = set(groups) if groups else None
    if skip_groups:
        selected = (selected or set(STEPS)) - skip_groups
    only = None if probes == all_probes else (probes or {NONE})
    run = {s.number for s in selected} if selected is not None else None
    return Selection(only, selected, skip_groups or None, run, keep_going or parallel,
                     parallel, max(1, workers), depth)


# -- one task -----------------------------------------------------------------------------

def check_task(task: Task, target: str, *, mode: Mode, runs: int, sel: Selection,
               fresh: bool = False, failed: bool = False, steps: bool = True) -> TaskView:
    """Run the six steps on one task. Passed steps whose files didn't change since the last
    run of the same kind come from that run, so a re-check after a fix resumes at the step
    that failed."""
    from rlgate.core.jobs import TASK
    token = TASK.set(task.id)
    try:
        return _check_task(task, target, mode=mode, runs=runs, sel=sel, fresh=fresh,
                           failed=failed, steps=steps)
    finally:
        TASK.reset(token)


def _check_task(task: Task, target: str, *, mode: Mode, runs: int, sel: Selection,
                fresh: bool = False, failed: bool = False, steps: bool = True) -> TaskView:
    from rlgate.pipeline import CheckRun, audit

    root = task_root(task)
    fp = store.task_fingerprint(root)
    fps = store.step_fingerprints(task)
    only = sel.only
    prev = None
    if failed:
        found = store.latest_for(task.id)
        if found is None:
            ui.notice(f"No earlier result for {task.id}; running every step.")
        else:
            prev = found[0]
            again = {c.probe_id for c in prev.checks if c.status == CheckRun.FLAGGED}
            if not again and prev.stopped_at is None:
                ui.notice(f"Nothing failed last time on {task.id}; showing that result. "
                          "Run without --failed to check everything again.")
                return _view(task, target, prev, found[1], cached=True, sel=sel,
                             full_possible=mode.full_possible, limit=mode.limit, missing=mode.missing)
            only = again or {NONE}

    shape = [sorted(sel.steps) if sel.steps else None, sel.keep_going, sel.depth]
    if sel.deep:
        shape.append({"deep": sel.deep})
    key = store.cache_key(fp, mode=mode.name, checks=sorted(only) if only else None,
                          runs=runs, extra=shape)
    if not fresh and prev is None:
        hit = store.load_cached(task.id, key)
        if hit is not None:
            return _view(task, target, hit[0], hit[1], cached=True, sel=sel,
                         full_possible=mode.full_possible, limit=mode.limit, missing=mode.missing)

    prior, reuse = (None, set()) if fresh or prev is not None else \
        _reusable(task, root, mode, runs, fps, sel)
    if steps and prior is not None:
        _say_resume(prior, reuse, sel)
    if mode.name == "full" and steps and not reuse:
        grades = (f", then grades the reference solution and a do-nothing submission {runs} "
                  "times each before the later steps" if _needs_baseline(sel) else
                  " (these checks only look at the environment, so nothing is graded)")
        ui.err().print(f"Full run on {_backend_name(mode.backend)}: builds every image{grades}. "
                       "--quick skips all of that.", style="dim", highlight=False)
    t0 = time.monotonic()
    try:
        result = audit(task, k=runs, only=only, backend=mode.backend,
                       static_only=mode.name == "quick", include_review=True,
                       env_options=mode.options, steps=sel.steps, keep_going=sel.keep_going,
                       prior=prior, reuse=reuse, fingerprints=fps,
                       on_findings=lambda fs: _waive(fs, root), workers=sel.workers,
                       depth=sel.depth, parallel=sel.parallel, review=sel.review)
    except Exception as e:  # one task's failure is reported, never a crash
        logging.getLogger("rlgate.run").exception("%s could not be checked", task.id)
        return TaskView(target, task.id, root, None, None, mode=mode.name,
                        task_type=task.source, error=_explain_error(e),
                        infra=is_infrastructure(e), seconds=time.monotonic() - t0)

    if prev is not None:
        result = _merge(prev, result, only or set())
        key = store.cache_key(fp, mode=mode.name, checks=None, runs=runs, extra=shape)
    extra = {"fingerprint": fp, "type": task.source, "root": str(root), "runs": runs,
             "partial": (prev is None and only is not None) or sel.steps is not None}
    store.save(result, key=key, target=target, mode=mode.name, origin=task.origin,
               extra=extra)
    d = store.result_to_dict(result, target=target, mode=mode.name, origin=task.origin,
                             extra=extra)
    view = _view(task, target, result, d, cached=False, sel=sel,
                 full_possible=mode.full_possible, limit=mode.limit, missing=mode.missing)
    view.created = d["created"]
    return view


def _waive(findings, root: Path) -> None:
    try:
        waivers.apply(findings, root)
    except waivers.WaiverError as e:
        raise CheckError(str(e), "fix or remove the [[waivers]] entry in rlgate.toml") from e


def _reusable(task: Task, root: Path, mode: Mode, runs: int, fps: dict[int, str],
              sel: Selection):
    """(the last comparable run, the steps that can come from it): the passed steps from
    step 1 on whose files are unchanged, up to the first step this run must do itself."""
    from rlgate.pipeline import CheckRun, StepRun, step_failed

    found = store.latest_for(task.id, mode=mode.name, runs=runs)
    if found is None:
        return None, set()
    prior, _ = found
    first = min(sel.steps) if sel.steps else len(STEPS) + 1
    reuse: set[int] = set()
    by_n = {s.number: s for s in prior.steps}
    for st in STEPS:
        sr = by_n.get(st.number)
        if sr is None or sr.state not in (StepRun.RAN, StepRun.CACHED) or \
                sr.fingerprint != fps[st.number]:
            break
        if sel.review is not None and any(          # --deep-review: its LLM checks must run
                c.status in (CheckRun.SKIPPED_DEEP, CheckRun.UNFINISHED)
                and Q.step_of_probe(c.probe_id) is st for c in prior.checks):
            break
        fs = [f for f in prior.findings if rules.step_of(f.rule_id) is st]
        for f in fs:
            f.waiver = None
        _waive(fs, root)
        if step_failed(fs):
            break
        if sel.steps is None or st.number < first:
            reuse.add(st.number)
    return (prior, reuse) if reuse else (None, set())


def _say_resume(prior, reuse: set[int], sel: Selection) -> None:
    if not reuse:
        return
    last = max(reuse)
    span = "Step 1" if last == 1 else f"Steps 1-{last}"
    was = prior.stopped_at
    if sel.steps is None and was is not None and was > last + 1:
        ui.notice(f"{span} unchanged since the last check; step {last + 1} runs again "
                  f"because its files changed (the last run stopped at step {was}).")
    else:
        ui.notice(f"{span} unchanged since the last check, taken from it; "
                  f"resuming at step {last + 1}.")


def _merge(prev, new, rerun: set[str]):
    """--failed: keep last time's clean checks, replace the re-run ones."""
    checks = [c for c in prev.checks if c.probe_id not in rerun] + \
        [c for c in new.checks if c.probe_id in rerun]
    baseline_rules = {r.id for r in rules.for_probe("baseline.invariant")}
    fresh_baseline = new.baseline is not None
    keep = [f for f in prev.findings if f.probe_id not in rerun
            and not (fresh_baseline and f.rule_id in baseline_rules)]
    findings = [f for f in new.findings if f.probe_id in rerun
                or f.rule_id in baseline_rules] + keep
    return replace(new, findings=findings, checks=checks,
                   baseline=new.baseline or prev.baseline,
                   baseline_sane=new.baseline_sane if fresh_baseline else prev.baseline_sane)


def _view(task: Task, target: str, result, d: dict, *, cached: bool, sel: Selection,
          full_possible: bool, limit: str | None = None,
          missing: str | None = None) -> TaskView:
    root = task_root(task)
    for f in result.findings:
        f.waiver = None
    try:
        stale = waivers.apply(result.findings, root)
    except waivers.WaiverError as e:
        raise CheckError(str(e), "fix or remove the [[waivers]] entry in rlgate.toml") from e
    mode = d.get("mode", "full")
    o = Q.outcome(result, quick=mode == "quick", full_possible=full_possible,
                  selected=sel.selected, why_no_full=limit or missing,
                  skipped_in_config=sel.skipped)
    return TaskView(target, task.id, root, result, o, mode=mode, cached=cached,
                    task_type=d.get("type") or task.source, stale=stale,
                    created=d.get("created", ""), limit=limit)


def saved_view(task: Task, target: str) -> TaskView | None:
    """The latest saved result for a task, with waivers applied now."""
    found = store.latest_for(task.id)
    if found is None:
        return None
    result, d = found
    return _view(task, target, result, d, cached=True, sel=Selection(),
                 full_possible=True)


def changed_since(view: TaskView, d_fingerprint: str | None) -> bool:
    return bool(d_fingerprint) and store.task_fingerprint(view.root) != d_fingerprint


def _explain_error(e: Exception) -> str:
    from rlgate.sandbox.errors import BackendUnavailable

    msg = str(e).strip() or type(e).__name__
    if isinstance(e, BackendUnavailable):
        return f"{msg}. Fix the environment (rlgate doctor), or run with --quick."
    return f"{type(e).__name__}: {msg}"


# -- a bank -------------------------------------------------------------------------------

def check_bank(tasks: list[Task], target: str, *, quick: bool, cfg: S.Settings, runs: int,
               only_groups: list[str] | None, fresh: bool, failed: bool, jobs: int,
               source: Source | None = None, step: int | None = None,
               start: int | None = None, keep_going: bool = False,
               parallel: bool = False, workers: int | None = None,
               depth: str | None = None, review: Any = None,
               on_done: Callable[[TaskView], None] | None = None
               ) -> tuple[list[TaskView], str, float]:
    from rlgate.core.registry import discover

    t0 = time.monotonic()
    rep = next((t for t in tasks if Capability.CONTAINER in t.capabilities), tasks[0])
    mode = decide_mode(rep, quick=quick, cfg=cfg)
    if mode.why_quick:
        ui.notice(mode.why_quick)
    elif mode.name == "full":
        k = runs or cfg.checks.runs
        ui.err().print(f"Full run of {len(tasks)} tasks on {_backend_name(mode.backend)}, "
                       f"{jobs} at a time: each runs steps 1-6, grading the reference and a "
                       f"do-nothing submission {k} times each. -j N runs N tasks at "
                       "once; --quick runs only the file-only checks (seconds).",
                       style="dim", highlight=False)
    discover()                      # warm the registry before threads

    def one(task: Task) -> tuple[TaskView, float]:
        ui.task_start(task.id)
        try:
            v = _one(task)
        finally:
            secs = ui.task_done()
        return v, secs

    def shown(v: TaskView, secs: float) -> TaskView:       # main thread only
        bad = [] if v.result is None else sorted({f.rule_id for f in v.result.findings
                                                  if f.rule_id and f.waiver is None})
        why = v.error.splitlines()[0] if v.error else (
            ", ".join(bad[:6]) + (f" +{len(bad) - 6} more" if len(bad) > 6 else ""))
        ui.task_line(v.task_id, v.verdict, secs, why)
        if on_done is not None:             # saved now: a stopped run keeps finished tasks
            try:
                on_done(v)
            except OSError as e:
                logging.getLogger("rlgate.run").warning(
                    "could not save %s's result: %s", v.task_id, e)
        return v

    def _one(task: Task) -> TaskView:
        tgt = task_target(task, target)
        try:
            c = S.task_checks(cfg.checks, task_root(task), cfg.files)
            sel = select(only_groups, c.skip, step=step, start=start, keep_going=keep_going,
                         parallel=parallel, workers=workers or c.workers,
                         depth=depth or c.depth)
            sel.review = review
            r = runs or c.runs
            v = check_task(task, tgt, mode=mode, runs=r, sel=sel, fresh=fresh,
                           failed=failed, steps=False)
            v.standalone = task.origin is not None
            return v
        except CheckError as e:
            return TaskView(tgt, task.id, task_root(task), None, None, mode=mode.name,
                            task_type=task.source, error=e.message)

    views: list[TaskView] = []
    with ui.task_board(len(tasks), jobs), ui.no_steps():
        if jobs > 1 and len(tasks) > 1:
            ex = cf.ThreadPoolExecutor(max_workers=jobs)
            try:
                futs = {ex.submit(one, t): t for t in tasks}
                for fut in cf.as_completed(futs):
                    views.append(shown(*fut.result()))
            except KeyboardInterrupt:
                # don't wait for tasks blocked in network calls; main() cleans up and exits
                ex.shutdown(wait=False, cancel_futures=True)
                raise
            ex.shutdown(wait=True)
        else:
            views.extend(shown(*one(t)) for t in tasks)
    views.sort(key=lambda v: v.task_id)
    store.set_last("bank", target, [{"task": v.task_id, "target": v.target} for v in views],
                   source=(source or Source()).as_dict())
    return views, mode.name, time.monotonic() - t0
