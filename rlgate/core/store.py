"""Saved results: what ``rlgate explain`` / ``report`` / ``check --failed`` read, and the cache
that makes re-checking an unchanged task instant.

    $RLGATE_STATE_DIR (default ./.rlgate)/
      results/<task-id>/<key>.json   one result per (task content, mode, checks, runs)
      last.json                      the last ``check``: a task or a bank, with result paths

The cache key covers everything that changes the answer: the task's files (path, size,
mtime — an edit invalidates it), the run mode, which checks ran, the number of baseline
runs, and the rlgate version. ``rlgate clean`` deletes ``results/`` and ``last.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from rlgate import __version__
from rlgate.core.context import Baseline
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.repro import state_dir

if TYPE_CHECKING:
    from rlgate.pipeline import AuditResult

_SKIP_DIRS = {".rlgate", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
SCHEMA = "rlgate.result/1"


def task_fingerprint(root: str | Path) -> str:
    """Hash of every file's (path, size, mtime) under ``root`` — cheap, and any edit,
    add or delete changes it."""
    root = Path(root)
    h = hashlib.sha256()
    if root.is_file():
        st = root.stat()
        h.update(f"{root.name}\0{st.st_size}\0{st.st_mtime_ns}".encode())
        return h.hexdigest()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if name == "rlgate.toml" and Path(dirpath) == root:
                continue                    # waivers don't change what the checks find
            try:
                st = p.lstat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def step_fingerprints(task) -> dict[int, str]:
    """Per step, a hash of the files its result depends on, so a re-check reuses a passed
    step whose files didn't change (and resumes at the failed one).

    build: what the images are built from and the task config; tests: the held-out tests and
    the verifier's build context; solution: the reference; instruction: the task statement;
    workspace: the agent's files when they live outside an image."""
    def h(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]

    def fp(paths) -> str:
        return h(*[f"{p}:{task_fingerprint(p)}" for p in sorted({str(x) for x in paths
                                                               if x and Path(x).exists()})])

    ws_env, g = task.workspace_env, task.grader
    build_paths = [ws_env.context if ws_env else None, g.env.context if g.env else None,
                   task.meta.get("config_file")]
    if g.image and Path(g.image).exists():
        build_paths.append(g.image)
    build = fp(build_paths)
    tests = fp([host for host, _ in g.inject] + [g.env.context if g.env else None])
    solution = h(fp([task.oracle[0]] if task.oracle else []),
                 task.gold_patch.sha256 if task.gold_patch else "")
    instruction = h(task.instruction or "")
    workspace = fp([task.repo_dir]) if ws_env is None else ""
    grader = h(g.run, str(g.timeout_s), repr(g.env_vars))
    # steps 2+ hold check results: a new rlgate version's checks must run again (the
    # build in step 1 is the task's own, whatever rlgate looks at afterwards)
    v = __version__
    return {
        1: h(build),
        2: h(build, tests, solution, workspace, grader, v),
        3: h(build, tests, solution, workspace, v),
        4: h(build, tests, solution, workspace, grader, v),
        5: h(build, tests, workspace, grader, v),
        6: h(tests, solution, instruction, workspace, v),
    }


def cache_key(fingerprint: str, *, mode: str, checks: list[str] | None, runs: int,
              extra=None) -> str:
    raw = json.dumps([fingerprint, mode, sorted(checks) if checks else None, runs,
                      __version__, extra])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _task_dir(task_id: str) -> Path:
    return state_dir() / "results" / re.sub(r"[^A-Za-z0-9._-]+", "_", task_id)


# -- (de)serialization ------------------------------------------------------------------


def finding_to_dict(f: Finding) -> dict:
    d = asdict(f)
    d["severity"] = f.severity.name
    d["verdict"] = f.verdict.value
    d["evidence"]["artifacts"] = [str(p) for p in f.evidence.artifacts]
    return d


def finding_from_dict(d: dict) -> Finding:
    ev = dict(d["evidence"])
    ev["artifacts"] = [Path(p) for p in ev.get("artifacts", [])]
    return Finding(
        probe_id=d["probe_id"], title=d["title"], severity=Severity[d["severity"]],
        verdict=VerdictTier(d["verdict"]), evidence=Evidence(**ev),
        location=d.get("location"), fix_hint=d.get("fix_hint"), rule_id=d.get("rule_id", ""),
        waiver=d.get("waiver"))


def result_to_dict(result, *, target: str, mode: str, origin: str | None,
                   extra: dict | None = None) -> dict:
    b = result.baseline
    return {**(extra or {}),
        "schema": SCHEMA,
        "rlgate": __version__,
        "task": result.task_id,
        "target": target,
        "origin": origin,
        "mode": mode,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seconds": round(getattr(result, "seconds", 0.0), 2),
        "baseline_sane": result.baseline_sane,
        "baseline": None if b is None else {
            "oracle": b.oracle, "nop": b.nop, "k": b.k, "pass_reward": b.pass_reward,
            "flaky": sorted(b.flaky_checks), "oracle_runs": list(b.oracle_runs),
            "nop_runs": list(b.nop_runs), "per_test": b.per_test,
            "nop_per_test": b.nop_per_test, "reports": b.reports,
            "fail_to_pass": b.fail_to_pass, "pass_to_pass": b.pass_to_pass,
            "grade_seconds": round(b.grade_seconds, 2), "grade_limit": b.grade_limit,
            "solve_seconds": round(b.solve_seconds, 2), "solve_limit": b.solve_limit,
            "extracted": b.extracted, "reference": b.reference},
        "stopped_at": getattr(result, "stopped_at", None),
        "steps": [{"step": sr.number, "name": sr.step.label, "question": sr.step.text,
                   "state": sr.state, "seconds": round(sr.seconds, 2),
                   "fingerprint": sr.fingerprint, "reason": sr.reason}
                  for sr in getattr(result, "steps", [])],
        "checks": [{"probe_id": c.probe_id, "family": c.family, "status": c.status,
                    "findings": c.findings, "seconds": round(getattr(c, "seconds", 0.0), 2),
                    "reason": getattr(c, "reason", ""), "note": getattr(c, "note", "")}
                   for c in result.checks],
        "findings": [finding_to_dict(f) for f in result.findings],
    }


def result_from_dict(d: dict) -> AuditResult:
    from rlgate.pipeline import AuditResult, CheckRun, StepRun

    b = d.get("baseline")
    baseline = None if b is None else Baseline(
        oracle=b["oracle"], nop=b["nop"], k=b["k"], pass_reward=b.get("pass_reward", 1.0),
        flaky_checks=frozenset(b.get("flaky", [])),
        oracle_runs=tuple(b.get("oracle_runs", [])), nop_runs=tuple(b.get("nop_runs", [])),
        per_test=dict(b.get("per_test") or {}), nop_per_test=dict(b.get("nop_per_test") or {}),
        reports=bool(b.get("reports", False)),
        grade_seconds=b.get("grade_seconds", 0.0), grade_limit=b.get("grade_limit", 0.0),
        solve_seconds=b.get("solve_seconds", 0.0), solve_limit=b.get("solve_limit", 0.0),
        extracted=b.get("extracted"), reference=b.get("reference", True))
    checks = [CheckRun(c["probe_id"], c["family"], c["status"], c.get("findings", 0),
                       seconds=c.get("seconds", 0.0), reason=c.get("reason", ""),
                       note=c.get("note", ""))
              for c in d.get("checks", [])]
    r = AuditResult(d["task"], d["baseline_sane"],
                    [finding_from_dict(f) for f in d.get("findings", [])], checks,
                    baseline=baseline, seconds=d.get("seconds", 0.0),
                    steps=[StepRun(x["step"], x["state"], x.get("seconds", 0.0),
                                   x.get("fingerprint", ""), x.get("reason", ""))
                           for x in d.get("steps", [])],
                    stopped_at=d.get("stopped_at"))
    return r


# -- cache ------------------------------------------------------------------------------


def save(result, *, key: str, target: str, mode: str, origin: str | None,
         extra: dict | None = None) -> Path:
    d = _task_dir(result.task_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}.json"
    path.write_text(json.dumps(result_to_dict(result, target=target, mode=mode,
                                              origin=origin, extra=extra), indent=2))
    os.utime(path)                  # newest = latest_for(), also when overwriting
    return path


def load_cached(task_id: str, key: str) -> tuple[AuditResult, dict] | None:
    path = _task_dir(task_id) / f"{key}.json"
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text())
        return result_from_dict(d), d
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest_for(task_id: str, *, mode: str | None = None,
               runs: int | None = None) -> tuple[AuditResult, dict] | None:
    """The most recent saved result for a task (of this mode and number of runs, when
    given) — preferring a complete one (every step) over a partial ``--only`` run."""
    d = _task_dir(task_id)
    found = []
    for f in (d.glob("*.json") if d.is_dir() else []):
        try:
            data = json.loads(f.read_text())
            if (mode and data.get("mode") != mode) or (
                    runs is not None and mode == "full" and data.get("runs") != runs):
                continue
            found.append((not data.get("partial", False), f.stat().st_mtime, data))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for _, _, data in sorted(found, key=lambda x: (x[0], x[1]), reverse=True):
        try:
            return result_from_dict(data), data
        except (KeyError, TypeError, ValueError):
            continue
    return None


def set_last(kind: str, target: str, entries: list[dict], source: dict | None = None
             ) -> None:
    sd = state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "last.json").write_text(json.dumps(
        {"kind": kind, "target": target, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "entries": entries, "source": source or {}}, indent=2))


def get_last() -> dict | None:
    p = state_dir() / "last.json"
    try:
        return json.loads(p.read_text()) if p.is_file() else None
    except (OSError, ValueError):
        return None


def clear() -> int:
    """Delete saved results (proofs for `rlgate repro` are kept). Returns files removed."""
    sd = state_dir()
    n = 0
    res = sd / "results"
    if res.is_dir():
        n = sum(1 for _ in res.rglob("*.json"))
        shutil.rmtree(res, ignore_errors=True)
    last = sd / "last.json"
    if last.is_file():
        last.unlink()
        n += 1
    return n
