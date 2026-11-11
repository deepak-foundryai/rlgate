"""Terminal output, built on Rich.

Rules every command follows:

1. Status symbols are Unicode with colour — ``✓`` passed, ``✗`` proven problem, ``?`` needs
   your judgment, ``!`` warning, ``-`` skipped — never emoji, so columns stay aligned in every
   terminal and in CI logs.
2. Every symbol is paired with a word; colour is never the only signal.
3. Colour and animation switch off automatically when output is not a terminal (CI, a pipe,
   a file); ``NO_COLOR`` and ``--no-color`` turn colour off; ``FORCE_COLOR`` turns it on.
4. ASCII fallback (``[ok]`` ``[fail]`` ``[review]`` ``[warn]`` ``[skip]``) with ``--ascii`` /
   ``RLGATE_ASCII=1``, or automatically when the terminal can't encode the symbols.

Results go to stdout; progress (spinners, per-check timings, the bank progress bar) goes to
stderr so it never mixes with a report that is piped or saved.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from rich import box as _box
from rich.console import Console
from rich.text import Text

_log = logging.getLogger("rlgate.run")

# statuses used across the product (core/questions.py uses the same words)
PASSED, PROVEN, JUDGMENT, WARNING, ACCEPTED, SKIPPED, NOT_YET = (
    "passed", "proven", "judgment", "warning", "accepted", "skipped", "not-yet")
NOT_RUN, PARTLY = "not-run", "partly"
# generic kinds for doctor-style lines
OK, FAIL, WARN, INFO = "ok", "fail", "warn", "info"

#            unicode  ascii       style
_STATUS = {
    PASSED:   ("✓", "[ok]",     "green"),
    ACCEPTED: ("✓", "[ok]",     "green"),
    PROVEN:   ("✗", "[fail]",   "bold red"),
    JUDGMENT: ("?", "[review]", "magenta"),
    WARNING:  ("!", "[warn]",   "yellow"),
    SKIPPED:  ("-", "[skip]",   "dim"),
    NOT_YET:  ("-", "[todo]",   "dim"),
    NOT_RUN:  ("-", "[skip]",   "dim"),
    PARTLY:   ("✓", "[part]",   "green"),
    OK:       ("✓", "[ok]",     "green"),
    FAIL:     ("✗", "[fail]",   "bold red"),
    WARN:     ("!", "[warn]",   "yellow"),
    INFO:     ("?", "[?]",      "dim"),
}

VERDICT_STYLE = {"Ready": "bold green", "Needs fixes": "bold yellow", "Broken": "bold red",
                 "Not checked": "bold"}


@dataclass
class _State:
    no_color: bool = False
    ascii: bool | None = None
    quiet: bool = False
    verbose: bool = False
    steps: bool = True
    out: Console | None = None
    err: Console | None = None


_S = _State()


def configure(*, no_color: bool = False, ascii: bool = False, quiet: bool = False,
              verbose: bool = False) -> None:
    """Called once by the CLI with the global flags."""
    _S.no_color = no_color
    _S.ascii = True if (ascii or os.environ.get("RLGATE_ASCII") == "1") else None
    _S.quiet = quiet
    _S.verbose = verbose
    _S.out = _S.err = None


def reset() -> None:
    configure()


def _console(stderr: bool) -> Console:
    plain = _S.no_color or "NO_COLOR" in os.environ
    force = True if os.environ.get("FORCE_COLOR") and not plain else None
    if plain:
        return Console(stderr=stderr, highlight=False, soft_wrap=False, no_color=True,
                       color_system=None, emoji=False)
    return Console(stderr=stderr, highlight=False, soft_wrap=False, force_terminal=force,
                   emoji=False)


def out() -> Console:
    # a fresh Console per call while unconfigured keeps pytest's capsys working (Rich binds
    # sys.stdout lazily, but the console object caches terminal detection)
    if _S.out is None or _S.out.file is not sys.stdout:
        _S.out = _console(False)
    return _S.out


def err() -> Console:
    if _S.err is None or _S.err.file is not sys.stderr:
        _S.err = _console(True)
    return _S.err


def verbose() -> bool:
    return _S.verbose


def quiet() -> bool:
    return _S.quiet


def ascii_on(console: Console | None = None) -> bool:
    if _S.ascii is not None:
        return _S.ascii
    enc = getattr((console or out()).file, "encoding", None) or "utf-8"
    try:
        "✓✗·—…".encode(enc)
    except (UnicodeEncodeError, LookupError):
        return True
    return False


def box():
    return _box.ASCII if ascii_on() else _box.ROUNDED


def sym(status: str, console: Console | None = None) -> Text:
    u, a, style = _STATUS[status]
    return Text(a if ascii_on(console) else u, style=style)


def style_of(status: str) -> str:
    return _STATUS[status][2]


def mark(kind: str, word: str = "", console: Console | None = None) -> Text:
    """``✓ ready`` — the symbol and its word, coloured together."""
    t = sym(kind, console)
    if word:
        t.append(" " + word, style=_STATUS[kind][2])
    return t


def dot() -> str:
    return "|" if ascii_on() else "·"


def dash() -> str:
    return "-" if ascii_on() else "—"


def echo(*parts, console: Console | None = None, style: str | None = None) -> None:
    """Print Text / plain strings (plain strings are never parsed as markup)."""
    c = console or out()
    t = Text()
    for p in parts:
        t.append(p if isinstance(p, Text) else Text(str(p)))
    c.print(t, style=style, overflow="fold", crop=False)


def error(message: str, next_step: str | None = None) -> None:
    """Every error says what to do next."""
    c = err()
    t = mark(FAIL, "error", c)
    t.append(f"  {message}")
    c.print(t, overflow="fold")
    if next_step:
        c.print(Text(f"  Next: {next_step}", style="bold"), overflow="fold")


def notice(message: str) -> None:
    c = err()
    t = mark(WARN, "note", c)
    t.append(f"  {message}")
    c.print(t, overflow="fold")


def duration(seconds: float) -> str:
    s = round(seconds)
    if seconds < 10:
        return f"{seconds:.1f}s"
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


# -- progress: per-check lines with timings (stderr) --------------------------------------

class _Step:
    def __init__(self) -> None:
        from rich.status import Status

        self.status: Status | None = None
        self.label = ""


_CUR = _Step()


def _fit(text: str, c: Console) -> Text:
    """A spinner line cut to the terminal width: a wrapped line can't be redrawn in place,
    so it would repeat down the screen."""
    room = max(10, (c.width or 80) - 4)            # the spinner and its space
    if len(text) > room:
        text = text[:room - 1] + ("." if ascii_on(c) else "…")
    return Text(text, no_wrap=True, overflow="ellipsis")


@contextmanager
def step(label: str):
    """One check of a full run: a spinner while it runs (terminals only), then a line with
    its time: ``  ✓ Reference solution passes   41.2s``. Also logged (the run's rlgate.log)."""
    t_log = time.monotonic()
    _log.info("start: %s", label)
    try:
        with _step(label):
            yield
    except BaseException as e:
        _log.info("failed after %.1fs: %s (%s: %s)", time.monotonic() - t_log, label,
                  type(e).__name__, str(e)[:500])
        raise
    _log.info("done in %.1fs: %s", time.monotonic() - t_log, label)


@contextmanager
def _step(label: str):
    trow = _task_row()
    if trow is not None and not _S.quiet:        # a bank run: the task board shows it
        with _TASKS_LOCK:
            trow.check, trow.detail = label, ""
        try:
            yield
        finally:
            with _TASKS_LOCK:
                trow.check = trow.detail = ""
                trow.checks += 1
        return
    if _S.quiet or not _S.steps:
        yield
        return
    row = _board_row()
    if row is not None:                  # steps running together: the board shows it
        with _BOARD_LOCK:
            row.check = label
        try:
            yield
        finally:
            with _BOARD_LOCK:
                row.check = ""
                row.detail = ""
        return
    c = err()
    t0 = time.monotonic()
    _CUR.label = label
    status = c.status(_fit(label, c), spinner="dots") if c.is_terminal else None
    if status is not None:
        status.start()
    _CUR.status = status
    ok = False
    na = ""
    unfinished = ""
    try:
        yield
        ok = True
    except Exception as e:
        from rlgate.core.probe import CouldNotJudge, NotApplicable
        if isinstance(e, NotApplicable):      # the check found it doesn't apply: not a failure
            na = str(e) or "doesn't apply"
        elif isinstance(e, CouldNotJudge):    # no verdict: shown as such, never as failed
            unfinished = str(e) or "no verdict"
        raise
    finally:
        if status is not None:
            status.stop()
        _CUR.status = None
        line = Text("  ")
        line.append_text(sym(SKIPPED if na else OK if ok else WARN if unfinished else FAIL,
                             c))
        line.append(f" {label}")
        if na:
            line.append(f"  (doesn't apply: {na})", style="dim")
        if unfinished:
            line.append(f"  (didn't finish: {unfinished})", style="dim")
        line.append(f"  {duration(time.monotonic() - t0)}", style="dim")
        c.print(line, overflow="fold")


def progress(msg: str) -> None:
    """A sub-step inside the current step (e.g. "reference solution, run 3 of 5"): updates
    the spinner; printed as its own line only with --verbose."""
    trow = _task_row()
    if trow is not None:
        with _TASKS_LOCK:
            trow.detail = msg
        return
    if _S.quiet or not _S.steps:
        return
    row = _board_row()
    if row is not None:
        with _BOARD_LOCK:
            row.detail = msg
        return
    if _CUR.status is not None:
        _CUR.status.update(_fit(f"{_CUR.label} {dash()} {msg}", err()))
    if _S.verbose:
        err().print(Text(f"      {msg}", style="dim"), overflow="fold")


# -- the step board: steps 3-6 running at the same time (parallel steps) ----------------------

@dataclass
class _Row:
    number: int
    label: str
    total: int
    state: str = "waiting"           # waiting | running | done | failed
    done: int = 0
    check: str = ""
    detail: str = ""
    t0: float = 0.0
    seconds: float = 0.0


class _Board:
    def __init__(self, rows: list[tuple[int, str, int]]) -> None:
        self.rows = {n: _Row(n, label, total) for n, label, total in rows}

    def __rich__(self):
        from rich.spinner import Spinner
        from rich.table import Table

        t = Table.grid(padding=(0, 2))
        for _ in range(5):
            t.add_column(no_wrap=True)
        with _BOARD_LOCK:
            rows = [dict(vars(r)) for r in self.rows.values()]
        for r in rows:
            name = Text(f" Step {r['number']}  {r['label']:<9}")
            secs = r["seconds"] if r["state"] in ("done", "failed") else (
                time.monotonic() - r["t0"] if r["t0"] else 0.0)
            count = Text(f"{r['done']} of {r['total']} checks", style="dim")
            if r["state"] == "running":
                mark_: object = Spinner("dots", text=Text("running", style="cyan"))
                now = r["check"] + (f" {dash()} {r['detail']}" if r["detail"] else "")
                what = Text(now, style="dim")
            elif r["state"] == "waiting":
                mark_, what = mark(SKIPPED, "waiting"), Text("")
            else:
                ok = r["state"] == "done"
                mark_ = mark(OK if ok else FAIL, "finished" if ok else "problems found")
                what = Text("")
            t.add_row(name, mark_, count, Text(duration(secs), style="dim"), what)
        return t


_BOARD: _Board | None = None
_BOARD_LOCK = threading.Lock()
_TL = threading.local()


def _board_row() -> _Row | None:
    b, n = _BOARD, getattr(_TL, "step", None)
    return b.rows.get(n) if b is not None and n is not None else None


@contextmanager
def board(rows: list[tuple[int, str, int]]):
    """A live board of steps running at the same time (stderr). Each row: the step, a
    spinner and the check it is on, ``done of total`` checks, elapsed time. Off a terminal,
    one line per step as it finishes."""
    global _BOARD
    if _S.quiet or not _S.steps:
        yield
        return
    from rich.live import Live

    c = err()
    b = _Board(rows)
    live = Live(b, console=c, refresh_per_second=8, transient=False) if c.is_terminal else None
    names = ", ".join(str(n) for n, _, _ in rows)
    c.print(Text(f"Running steps {names} at the same time; every result is shown at the end.",
                 style="dim"), overflow="fold")
    _BOARD = b
    if live is not None:
        live.start()
    try:
        yield
    finally:
        _BOARD = None
        if live is not None:
            live.stop()


def board_step(number: int) -> None:
    """Called by the thread that runs step ``number``: its checks update that row."""
    _TL.step = number
    b = _BOARD
    if b is not None and number in b.rows:
        with _BOARD_LOCK:
            b.rows[number].state = "running"
            b.rows[number].t0 = time.monotonic()


def board_tick() -> None:
    """One more check of the calling thread's step is finished (passed, flagged, n/a)."""
    row = _board_row()
    if row is not None:
        with _BOARD_LOCK:
            row.done += 1


def board_done(number: int, failed: bool) -> None:
    _TL.step = None
    b = _BOARD
    if b is None or number not in b.rows:
        return
    with _BOARD_LOCK:
        r = b.rows[number]
        r.state = "failed" if failed else "done"
        r.seconds = time.monotonic() - r.t0
        r.check = r.detail = ""
    c = err()
    if not c.is_terminal:
        line = Text(f"  Step {number} {r.label}  ")
        line.append_text(mark(FAIL if failed else OK,
                              "problems found" if failed else "finished", c))
        line.append(f"  {r.done} of {r.total} checks  {duration(r.seconds)}", style="dim")
        c.print(line, overflow="fold")


# -- the task board: a bank's tasks running at the same time (--jobs N) ---------------------

@dataclass
class _TaskRow:
    name: str
    t0: float
    step: int = 0
    step_label: str = ""
    check: str = ""
    detail: str = ""
    checks: int = 0


class _TaskBoard:
    def __init__(self, total: int, jobs: int) -> None:
        self.total, self.jobs = total, jobs
        self.t0 = time.monotonic()
        self.rows: dict[int, _TaskRow] = {}          # thread id → row
        self.done = 0
        self.counts: dict[str, int] = {}

    def __rich__(self):
        from rich.spinner import Spinner
        from rich.table import Table

        with _TASKS_LOCK:
            rows = sorted((dict(vars(r)) for r in self.rows.values()), key=lambda r: r["t0"])
            done, counts = self.done, dict(self.counts)
        c = err()
        head = Text(f" {done} of {self.total} tasks done", style="bold")
        head.append(f"  {dot()}  {len(rows)} running  {dot()}  ", style="dim")
        for verdict, word, kind in (("Ready", "ready", OK), ("Needs fixes", "need fixes", WARN),
                                    ("Broken", "broken", FAIL),
                                    ("Not checked", "not checked", NOT_RUN)):
            if kind == NOT_RUN and not counts.get(verdict):
                continue
            head.append_text(mark(kind, f"{counts.get(verdict, 0)} {word}", c))
            head.append("  ")
        head.append(duration(time.monotonic() - self.t0), style="dim")
        room = max(3, (c.height or 30) - 4)
        width = max(12, min(40, max((len(r["name"]) for r in rows), default=12)))
        t = Table.grid(padding=(0, 1), expand=True)
        t.add_column(width=2, no_wrap=True)
        t.add_column(width=width, no_wrap=True, overflow="ellipsis")
        t.add_column(width=17, no_wrap=True, overflow="ellipsis")
        t.add_column(width=7, no_wrap=True, justify="right")
        t.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for r in rows[:room]:
            step = (Text(f"Step {r['step']} {r['step_label']}") if r["step"] else
                    Text("starting", style="dim"))
            now = r["check"] + (f" {dash()} {r['detail']}" if r["detail"] else "")
            t.add_row(Spinner("dots"), Text(r["name"]), step,
                      Text(duration(time.monotonic() - r["t0"]), style="dim"),
                      Text(now, style="dim"))
        if len(rows) > room:
            t.add_row(Text(""), Text(f"… {len(rows) - room} more running", style="dim"),
                      Text(""), Text(""), Text(""))
        from rich.console import Group
        return Group(head, t)


_TASKS: _TaskBoard | None = None
_TASKS_LOCK = threading.Lock()
_TASKS_LIVE = None


def _task_row() -> _TaskRow | None:
    b = _TASKS
    return b.rows.get(threading.get_ident()) if b is not None else None


@contextmanager
def task_board(total: int, jobs: int):
    """A live board while a bank's tasks run in parallel (stderr): a header with how many
    are done and their verdicts, one row per running task (its step, its current check,
    elapsed time), and a permanent line for each task as it finishes. Off a terminal, only
    the finished lines."""
    global _TASKS, _TASKS_LIVE
    if _S.quiet:
        yield
        return
    from rich.live import Live

    c = err()
    _TASKS = _TaskBoard(total, jobs)
    _TASKS_LIVE = Live(_TASKS, console=c, refresh_per_second=4, transient=True,
                       redirect_stderr=False) if c.is_terminal else None
    if _TASKS_LIVE is not None:
        _TASKS_LIVE.start()
    try:
        yield
    finally:
        if _TASKS_LIVE is not None:
            _TASKS_LIVE.stop()
        _TASKS = _TASKS_LIVE = None


def task_start(name: str) -> None:
    """Called by the thread that checks task ``name``."""
    b = _TASKS
    if b is not None:
        with _TASKS_LOCK:
            b.rows[threading.get_ident()] = _TaskRow(name, time.monotonic())


def task_step(number: int, label: str) -> None:
    """The calling thread's task moved on to step ``number``."""
    row = _task_row()
    if row is not None:
        with _TASKS_LOCK:
            row.step, row.step_label, row.check, row.detail = number, label, "", ""


def task_done() -> float:
    """The calling thread's task is done: drop its row; returns its seconds."""
    b = _TASKS
    if b is None:
        return 0.0
    with _TASKS_LOCK:
        row = b.rows.pop(threading.get_ident(), None)
    return time.monotonic() - row.t0 if row else 0.0


def task_line(name: str, verdict: str, seconds: float, detail: str = "") -> None:
    """A finished task's permanent line. Call it from the thread that opened the board:
    the live board redraws from there too, so the two never interleave."""
    b = _TASKS
    if b is None:
        return
    with _TASKS_LOCK:
        b.done += 1
        b.counts[verdict] = b.counts.get(verdict, 0) + 1
        n = b.done
    kind = {"Ready": OK, "Needs fixes": WARN, "Not checked": NOT_RUN}.get(verdict, FAIL)
    c = _TASKS_LIVE.console if _TASKS_LIVE is not None else err()
    line = Text(f"  [{n}/{b.total}] ")
    line.append_text(mark(kind, verdict, c))
    line.append(f"  {name}")
    line.append(f"  {duration(seconds)}", style="dim")
    if detail:
        line.append(f"  {detail}", style="dim")
    c.print(line, overflow="ellipsis", no_wrap=True)


@contextmanager
def no_steps():
    """Inside a bank run: no per-check lines (tasks run in parallel); the bar shows progress."""
    before = _S.steps
    _S.steps = False
    try:
        yield
    finally:
        _S.steps = before


@contextmanager
def bar(total: int, label: str):
    """A progress bar over a bank's tasks; yields ``advance(task_id)``."""
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    c = err()
    if _S.quiet or not c.is_terminal:
        done = [0]

        def adv(name: str) -> None:
            done[0] += 1
            if not _S.quiet:
                c.print(Text(f"  [{done[0]}/{total}] {name}", style="dim"), overflow="fold")
        yield adv
        return
    with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                  TimeElapsedColumn(), console=c, transient=True) as p:
        tid = p.add_task(label, total=total)

        def adv(name: str) -> None:
            p.update(tid, advance=1, description=f"{label} {dot()} {name}")
        yield adv
