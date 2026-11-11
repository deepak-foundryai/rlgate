"""One folder per ``rlgate check`` run, named and laid out for people (as Harbor's jobs/).

    <jobs dir> (--jobs-dir, [checks] jobs_dir, $RLGATE_JOBS_DIR; default ./rlgate-jobs)/
      2026-11-11__19-44-24__mimo-harbor/        <date>__<time>__<--job-name or target>
        config.json        the command, target, options and rlgate version
        result.json        the verdict: every task (a bank) or the one task, with findings
        report.md          the same, readable (a bank table, or the task's QA card)
        report.html        a filterable report to share
        rlgate.log         everything rlgate logged during the run (retries, timings, errors)
        tasks/
          <task-id>/
            result.json    that task's verdict, steps, findings with proofs
            report.md      that task's QA card

The cache that makes re-checks instant stays in ``.rlgate/`` (internal, hashed names).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rlgate import __version__

if TYPE_CHECKING:
    from rlgate.core.report import TaskView

ENV = "RLGATE_JOBS_DIR"
DEFAULT = "rlgate-jobs"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")[:60] or "run"


class Job:
    """A run's folder. ``open`` creates it and starts the log; ``save`` writes the results."""

    def __init__(self, path: Path, handler: logging.Handler) -> None:
        self.path = path
        self._handler = handler

    @classmethod
    def open(cls, jobs_dir: str | Path | None, *, target: str, name: str | None,
             options: dict, argv: list[str]) -> Job:
        root = Path(jobs_dir or os.environ.get(ENV) or DEFAULT).expanduser()
        stamp = datetime.now().strftime("%Y-%m-%d__%H-%M-%S")
        base = f"{stamp}__{_slug(name or Path(target.rstrip('/')).name)}"
        path, n = root / base, 1
        while path.exists():                   # two runs in the same second
            n += 1
            path = root / f"{base}-{n}"
        (path / "tasks").mkdir(parents=True)
        _write(path / "config.json", {
            "command": shlex.join(["rlgate", *argv]),
            "target": str(Path(target).expanduser().resolve()),
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rlgate": __version__,
            "options": options,
        })
        handler = logging.FileHandler(path / "rlgate.log", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
        log = logging.getLogger("rlgate")
        log.addHandler(handler)
        if log.level == logging.NOTSET or log.level > logging.DEBUG:
            log.setLevel(logging.DEBUG)
        if not any(getattr(h, "_rlgate_console", False) for h in log.handlers):
            console = logging.StreamHandler()     # keep warnings on the terminal, as before
            console.setLevel(logging.WARNING)
            console.setFormatter(_OneLine())
            console._rlgate_console = True        # type: ignore[attr-defined]
            log.addHandler(console)
        return cls(path, handler)

    def save(self, views: list[TaskView], *, kind: str, target: str,
             seconds: float = 0.0) -> None:
        from rlgate.core import report as R
        from rlgate.core.html import bank_html

        for v in views:
            d = self.path / "tasks" / _slug(v.task_id)
            d.mkdir(parents=True, exist_ok=True)
            _write(d / "result.json", R.task_dict(v))
            (d / "report.md").write_text(R.qa_card(v), encoding="utf-8")
        if kind == "bank":
            summary = R.bank_dict(views, target)
            md = R.bank_markdown(views, target)
        else:
            summary, md = R.task_dict(views[0]), R.qa_card(views[0])
        if seconds:
            summary = {**summary, "seconds": round(seconds, 1)}
        _write(self.path / "result.json", summary)
        (self.path / "report.md").write_text(md, encoding="utf-8")
        (self.path / "report.html").write_text(bank_html(views, target), encoding="utf-8")

    def close(self) -> None:
        log = logging.getLogger("rlgate")
        log.removeHandler(self._handler)
        self._handler.close()


class _OneLine(logging.Formatter):
    """The terminal gets the first line of a warning, never output or a traceback (they
    would break the live view); rlgate.log keeps everything."""

    def format(self, record: logging.LogRecord) -> str:
        lines = record.getMessage().strip().splitlines() or [""]
        first = lines[0][:200]
        return first + (" (details in rlgate.log)" if len(lines) > 1 or record.exc_info
                        else "")


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
