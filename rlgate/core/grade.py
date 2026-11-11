"""The one definition of "did this workspace pass the grader?".

Before this module the baseline, the variant engine and the forgery probes each decided pass
independently (exit code only / report-then-exit / exit code only), so a forged pass could be
judged by a different rule than the baseline it is compared against. Everything now goes
through ``run_grader``:

  - the baseline (oracle / do-nothing runs),
  - the variant engine (revert / mutation / equivalence variants; it may pass a subset
    command, and layers its own per-test discrimination on ``GradeResult.report``),
  - payload probes (forgery), which always use the FULL grader command — the real path.

How a pass is decided follows ``GraderSpec.verdict`` (what the real grader trusts):
  - ``exit_code`` (default): exit 0 → pass_reward, else 0.0;
  - ``report``: the report was produced, ≥1 test passed and none failed;
  - ``both``: exit 0 AND the report condition;
  - ``reward_file``: the grader writes its own reward (``GraderSpec.reward_files``, stale
    ones removed first). A ``.json`` file holds ``{name: number}`` — THE reward is
    ``reward_key`` (or the only entry) and every entry is kept as a metric; any other file
    holds one number. A missing/empty/garbled file scores 0.0 and says why.
"""

from __future__ import annotations

import json
import math
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rlgate.sandbox.parsers import TestReport, collect

if TYPE_CHECKING:
    from rlgate.core.task import Task
    from rlgate.sandbox.base import Overlay

VERDICT_MODES = ("exit_code", "report", "both", "reward_file")


@dataclass
class GradeResult:
    reward: float
    exit_code: int
    report: TestReport | None
    stdout: str
    stderr: str
    cmd: str
    pass_reward: float
    metrics: dict[str, float] = field(default_factory=dict)   # every reported reward entry
    reward_error: str | None = None     # verdict=reward_file: why no reward could be read

    @property
    def passed(self) -> bool:
        return self.reward >= self.pass_reward - 1e-9

    @property
    def did_not_run(self) -> bool:
        """The grader command itself couldn't start (missing interpreter/script)."""
        return self.exit_code == 127 or "No such file" in self.stderr


def run_grader(overlay: Overlay, task: Task, cmd: str | None = None) -> GradeResult:
    """Run the grader in ``overlay`` and score it. ``cmd`` defaults to the full grader."""
    g = task.grader
    cmd = cmd or g.run
    if g.verdict not in VERDICT_MODES:
        raise ValueError(f"grader.verdict must be one of {VERDICT_MODES}, got {g.verdict!r}")
    overlay.before_grade()
    if g.reward_files:
        overlay.exec("rm -f -- " + " ".join(shlex.quote(f) for f in g.reward_files), timeout=60)
    res = overlay.exec(_with_env(cmd, g.env_vars), timeout=g.timeout_s)
    report = collect(overlay, g.report) if g.report else None
    if g.verdict == "reward_file":
        reward, metrics, err = read_reward(overlay, g.reward_files, g.reward_key)
        return GradeResult(reward, res.exit_code, report, res.stdout, res.stderr, cmd,
                           g.pass_reward, metrics, err)
    reward = g.pass_reward if _passes(g.verdict, res.exit_code, report) else 0.0
    return GradeResult(reward, res.exit_code, report, res.stdout, res.stderr, cmd,
                       g.pass_reward)


def _with_env(cmd: str, env_vars: tuple[tuple[str, str], ...]) -> str:
    if not env_vars:
        return cmd
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env_vars)
    return f"export {exports}; {cmd}"


def read_reward(overlay: Overlay, files: tuple[str, ...],
                key: str = "reward") -> tuple[float, dict[str, float], str | None]:
    """(reward, metrics, error) from the first reward file that exists.

    A ``.json`` with several entries and none named ``key`` contributes its metrics but not
    the reward; the next file (e.g. Harbor's ``reward.txt``) then supplies the scalar."""
    metrics: dict[str, float] = {}
    for path in files:
        r = overlay.exec(f"cat {shlex.quote(path)}", timeout=60)
        if r.exit_code != 0:
            continue
        try:
            reward, found = parse_reward(r.stdout, json_file=path.endswith(".json"), key=key)
        except _NoKey as e:
            metrics.update(e.metrics)
            continue
        except ValueError as e:
            return 0.0, metrics, f"{path}: {e}"
        return reward, {**found, **metrics}, None
    if metrics:
        return 0.0, metrics, f"no {key!r} entry among {sorted(metrics)}"
    return 0.0, {}, f"no reward file was written ({', '.join(files) or 'none declared'})"


class _NoKey(ValueError):
    def __init__(self, key: str, metrics: dict[str, float]) -> None:
        super().__init__(f"no {key!r} entry among {sorted(metrics)}")
        self.metrics = metrics


def parse_reward(text: str, *, json_file: bool,
                 key: str = "reward") -> tuple[float, dict[str, float]]:
    if not text.strip():
        raise ValueError("empty reward file")
    if not json_file:
        try:
            value = float(text.strip())
        except ValueError:
            raise ValueError(f"not a number: {text.strip()[:60]!r}") from None
        if not math.isfinite(value):
            raise ValueError(f"non-finite reward {value!r}")
        return value, {"reward": value}
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if not isinstance(data, dict):
        data = {"reward": data}
    metrics: dict[str, float] = {}
    for name, v in data.items():
        if isinstance(v, bool):
            v = float(v)
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ValueError(f"non-numeric reward {name}={v!r}")
        metrics[str(name)] = float(v)
    if key in metrics:
        return metrics[key], metrics
    if len(metrics) == 1:
        return next(iter(metrics.values())), metrics
    raise _NoKey(key, metrics)


def _passes(mode: str, exit_code: int, report: TestReport | None) -> bool:
    if mode not in VERDICT_MODES:
        raise ValueError(f"grader.verdict must be one of {VERDICT_MODES}, got {mode!r}")
    exit_ok = exit_code == 0
    report_ok = report is not None and bool(report.passed) and not report.failed
    if mode == "exit_code":
        return exit_ok
    if mode == "report":
        return report_ok
    return exit_ok and report_ok


# -- the verdict cache -----------------------------------------------------------------------
#
# Agents grade similar candidates again and again (the same patch after a discard, a saved
# hack on re-check, two audits trying the same shortcut). Grading a patch on the pristine
# workspace is deterministic once the baseline found no flaky checks, so the verdict is
# cached on disk, keyed by the task's files, the grading command and the patch.

def _verdict_key(task: Task, patch: str | None, cmd: str | None) -> str | None:
    import hashlib

    from rlgate.core.store import task_fingerprint
    if not task.origin:
        return None
    try:
        fp = task_fingerprint(task.origin)
    except OSError:
        return None
    g = task.grader
    raw = "\0".join([fp, cmd or g.run, g.verdict, str(g.pass_reward), patch or ""])
    return hashlib.sha256(raw.encode(errors="replace")).hexdigest()[:32]


def cached_grade(sandbox, task: Task, patch: str | None, *, cmd: str | None = None,
                 deterministic: bool = True) -> GradeResult:
    """``run_grader`` on a fresh overlay with ``patch``, through the verdict cache when the
    grade is ``deterministic`` (no flaky checks in the baseline). Raises ``PatchConflict``
    like ``sandbox.overlay``."""
    import json

    from rlgate.core.repro import state_dir
    from rlgate.sandbox.parsers.base import TestCase, TestReport, TestStatus

    key = _verdict_key(task, patch, cmd) if deterministic else None
    path = state_dir() / "verdicts" / f"{key}.json" if key else None
    if path is not None:
        try:
            d = json.loads(path.read_text())
            rep = TestReport([TestCase(n, TestStatus(s)) for n, s in d["report"]]) \
                if d["report"] is not None else None
            return GradeResult(d["reward"], d["exit_code"], rep, d["stdout"], d["stderr"],
                               d["cmd"], d["pass_reward"], d["metrics"], d["reward_error"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    with sandbox.overlay(patch or None) as ovl:
        g = run_grader(ovl, task, cmd)
    if path is not None and not g.did_not_run:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "reward": g.reward, "exit_code": g.exit_code,
                "report": [[c.name, c.status.value] for c in g.report.cases]
                if g.report is not None else None,
                "stdout": g.stdout[-4000:], "stderr": g.stderr[-4000:], "cmd": g.cmd,
                "pass_reward": g.pass_reward, "metrics": g.metrics,
                "reward_error": g.reward_error}))
        except OSError:
            pass
    return g
