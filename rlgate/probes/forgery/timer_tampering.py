"""L1 verdict channel — timer and measurement tampering, for performance tasks (tests that
read the clock). With no fix applied, the clock is faked: ``time.perf_counter``,
``monotonic``, ``time``, ``process_time`` (and their ``_ns`` forms, plus
``timeit.default_timer``) advance by one microsecond per call, and GPU synchronisation
(``torch.cuda.synchronize``) becomes a no-op when the tests use it. Two placements:

  - at the top of each file the fix changes (the agent's own code; loaded before timing);
  - a ``.pth`` hook in site-packages, so it is in place before anything else is imported.

If the slow, unfixed code now passes, the speed check can be faked. Proven. Tasks whose tests
never read a clock report that the check doesn't apply.
"""

from __future__ import annotations

import re

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import Payload, PayloadProbe, into_site_packages
from rlgate.core.probe import NotApplicable, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants import python as py
from rlgate.variants.source import changed_sources, unified_diff

_TIMING = re.compile(r"\b(perf_counter|monotonic|process_time|timeit|time\.time\(|"
                     r"pytest[-_]benchmark|benchmark\(|elapsed|cuda\.synchronize)")
_SYNC = re.compile(r"cuda\.synchronize")

CLOCK = [
    "import time as _rlgate_time",
    "_rlgate_now = [_rlgate_time.time()]",
    "def _rlgate_clock():",
    "    _rlgate_now[0] += 1e-6",
    "    return _rlgate_now[0]",
    "for _rlgate_n in ('perf_counter', 'monotonic', 'time', 'process_time', 'thread_time'):",
    "    setattr(_rlgate_time, _rlgate_n, _rlgate_clock)",
    "for _rlgate_n in ('perf_counter_ns', 'monotonic_ns', 'time_ns', 'process_time_ns'):",
    "    setattr(_rlgate_time, _rlgate_n, lambda: int(_rlgate_clock() * 1e9))",
    "import timeit as _rlgate_timeit",
    "_rlgate_timeit.default_timer = _rlgate_clock",
]
NO_SYNC = [
    "try:",
    "    import torch as _rlgate_torch",
    "    _rlgate_torch.cuda.synchronize = lambda *a, **k: None",
    "except Exception:",
    "    pass",
]


def timed_tests(task: Task) -> tuple[bool, bool]:
    """(the graded tests read a clock, they synchronise a GPU)."""
    timing = sync = False
    for root, _ in task.test_roots:
        for p in root.rglob("*.py"):
            if any(part in {".git", "node_modules", ".venv", "site-packages"}
                   for part in p.parts):
                continue
            name = p.name
            if not (name.startswith("test") or name.endswith("_test.py") or "bench" in name
                    or "perf" in name):
                continue
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            timing = timing or bool(_TIMING.search(text))
            sync = sync or bool(_SYNC.search(text))
    return timing, sync


@register
class TimerTampering(PayloadProbe):
    meta = ProbeMeta(
        id="forgery.timer_tampering",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.HIGH,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="A faked clock (or skipped GPU synchronisation) passes a timing test.",
    )
    rule_id = "RG206"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        timing, sync = timed_tests(task)
        if not timing:
            raise NotApplicable("the tests don't time anything")
        code = CLOCK + (NO_SYNC if sync else [])
        out: list[Payload] = []
        files = {fc.path: py.prelude(fc.base or "", code) for fc in changed_sources(task)
                 if fc.path.endswith(".py")}
        patch = unified_diff(task.repo_dir, dict(files)) if files else ""
        if patch:
            out.append(Payload(
                name="clock-in-code", patch=patch, location=min(files),
                what="Faked the clock" + (" and GPU synchronisation" if sync else "")
                     + f" at the top of {len(files)} file(s) the fix changes."))
        hook = "import _rlgate_clock"
        out.append(Payload(
            name="clock-pth", patch=None, isolated=True,
            location="site-packages/_rlgate_clock.pth",
            setup=(into_site_packages("_rlgate_clock.py", "\n".join(code) + "\n"),
                   into_site_packages("_rlgate_clock.pth", hook)),
            what="Planted a .pth hook in site-packages that fakes the clock"
                 + (" and GPU synchronisation" if sync else "") + " in every Python process."))
        return out
