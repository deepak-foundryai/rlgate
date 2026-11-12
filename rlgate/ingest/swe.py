"""Native loader for SWE-bench-style rows (one task per JSONL line), graded the way the
SWE-bench harness grades them.

A row needs what the current SWE-bench datasets ship (SWE-bench, _Lite, _Verified,
_Multilingual): ``instance_id``, ``problem_statement``, ``patch`` (the reference fix),
``image`` / ``image_name`` (the prebuilt evaluation image), ``eval_script`` (applies the
hidden test patch, runs the tests between ">>>>> Start/End Test Output" markers),
``log_parser`` (which SWE-bench parser reads that output), ``FAIL_TO_PASS`` /
``PASS_TO_PASS`` and ``eval_type``. What it maps to:

  - the workspace is the image's repo (``/testbed``, or wherever the eval script ``cd``s);
  - the eval script is held out: it reaches the container only when grading starts, so
    the agent never sees the hidden tests it carries;
  - a pass is SWE-bench's RESOLVED_FULL: the log is parsed with the row's parser (an
    invalid run — patch didn't apply, no markers, a test command that failed while the log
    shows no failure — is no report at all) and every FAIL_TO_PASS test must pass and no
    PASS_TO_PASS test fail (``core.grade.tests_resolved``).

Rows from older dumps without ``eval_script`` / ``log_parser`` are loaded for the file-only
checks, with the reason in ``meta["unsupported"]``; the benchmarks' ``fetch.py`` adds both.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

from rlgate.core.task import EnvSpec, GraderSpec, Patch, ReportSpec, Task

EVAL_DIR = "/rlgate-swe"                  # where the held-out eval script lands at grading
LOG = f"{EVAL_DIR}/out/test_output.txt"   # its whole output (what SWE-bench parses)
SUBMISSION = "/rlgate-submission/model.patch"   # the work, as the patch the harness grades
APPLY_FAILED = ">>>>> Patch Apply Failed"
# SWE-bench's GIT_APPLY_CMDS, tried in order
_APPLY = ("git apply --verbose", "git apply --verbose --reject", "patch --batch --fuzz=5 -p1 -i")
_CD = re.compile(r"^\s*cd\s+(/[^\s;&|]+)\s*$", re.MULTILINE)
_LANG = {"python": "python", "py": "python", "js": "javascript", "javascript": "javascript",
         "ts": "typescript", "typescript": "typescript", "go": "go", "golang": "go",
         "rust": "rust", "rs": "rust", "java": "java", "kotlin": "kotlin", "php": "php",
         "ruby": "ruby", "rb": "ruby", "c": "c", "cpp": "cpp", "c++": "cpp",
         "csharp": "csharp", "c#": "csharp", "scala": "scala", "swift": "swift"}
_PARSER_LANG = {"parse_log_cargo": "rust", "parse_log_gotest": "go", "parse_log_maven": "java",
                "parse_log_gradle_custom": "java", "parse_log_ant": "java",
                "parse_log_phpunit": "php", "parse_log_jest": "javascript",
                "parse_log_vitest": "javascript", "parse_log_minitest": "ruby",
                "parse_log_rspec_transformed_json": "ruby", "parse_log_googletest": "cpp"}


class SweFormatError(ValueError):
    pass


def is_bank(path: str | Path) -> bool:
    """A .jsonl/.json file (or a folder of them) whose first row looks like a SWE row."""
    p = Path(path)
    files = [p] if p.is_file() else sorted(p.glob("*.jsonl")) if p.is_dir() else []
    for f in files:
        if f.suffix not in (".jsonl", ".json"):
            continue
        try:
            row = next(_rows(f), None)
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and "instance_id" in row and (
                "patch" in row or "eval_script" in row):
            return True
    return False


def load_bank(target: str | Path, cache: Path | None = None) -> Iterator[Task]:
    p = Path(target).expanduser().resolve()
    files = [p] if p.is_file() else sorted(p.glob("*.jsonl"))
    seen: dict[str, int] = {}
    for f in files:
        for row in _rows(f):
            t = load_row(row, f, cache=cache)
            n = seen.get(t.id, 0) + 1
            seen[t.id] = n
            if n > 1:                     # the same instance twice (e.g. two image tags)
                t = _renamed(t, f"{t.id}#{n}")
            yield t


def load_row(row: dict, source: Path, cache: Path | None = None) -> Task:
    if "instance_id" not in row:
        raise SweFormatError(f"{source}: a row has no instance_id")
    iid = str(row["instance_id"])
    tid = str(row.get("rlgate_id") or row.get("external_audit_id") or iid)
    unsupported: list[str] = []
    image = str(row.get("image_name") or row.get("image") or "") or None
    script = row.get("eval_script") or None
    parser = row.get("log_parser") or None
    if image is None:
        unsupported.append("no evaluation image (image / image_name)")
    if script is None:
        unsupported.append("no eval_script — use a current SWE-bench dataset revision, or "
                           "benchmarks/fetch.py, which adds one")
    if parser is None:
        unsupported.append("no log_parser — rlgate can't read the test output")
    else:
        from rlgate.sandbox.parsers.swebench_logs import PARSER_REGISTRY
        if parser not in PARSER_REGISTRY:
            unsupported.append(f"unknown log_parser {parser!r}")
            parser = None

    workdir = str(row.get("workdir") or _workdir(script) or "/testbed")
    f2p, p2p = _tests(row.get("FAIL_TO_PASS")), _tests(row.get("PASS_TO_PASS"))
    eval_type = str(row.get("eval_type") or "pass_and_fail")
    inject: tuple[tuple[str, str], ...] = ()
    report = None
    if script and parser:
        from rlgate.sandbox.parsers.swebench import record_exit_code, spec_format
        inject = ((str(_held_out(record_exit_code(script), cache)), EVAL_DIR),)
        report = ReportSpec(spec_format(parser, repo=str(row.get("repo") or ""),
                                        version=str(row.get("version") or ""),
                                        instance_id=iid), LOG)
    env = EnvSpec(image=image, workdir=workdir) if image else None
    grader = GraderSpec(
        run=_grade_cmd(workdir),
        env=env,                           # a fresh copy of the image, as the harness grades
        submission=SUBMISSION,
        artifacts=(str(Path(SUBMISSION).parent),),
        collect=((_collect_cmd(workdir, row.get("base_commit")), 600),),
        verdict="tests",
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        eval_type=eval_type,
        report=report,
        inject=inject,
        cwd=workdir,
        timeout_s=int(row.get("timeout") or 1800),
    )
    gold = row.get("patch") or None
    meta = {
        "format": "swebench",
        "name": iid,
        "instance_id": iid,
        "repo": row.get("repo"),
        "version": row.get("version"),
        "log_parser": row.get("log_parser"),
        "eval_type": eval_type,
        "fail_to_pass": len(f2p),
        "pass_to_pass": len(p2p),
        "config_file": str(source),
        "agent_timeout_s": float(row.get("agent_timeout") or 3600),
        # no "limits": a SWE row has no place to declare them (the harness sets them), so
        # RG012 would fire on every row of every dataset
    }
    for k in ("external_audit_id", "impossible_type", "difficulty", "created_at"):
        if row.get(k) is not None:
            meta[k] = row[k]
    if unsupported:
        meta["unsupported"] = unsupported
    return Task(
        id=tid,
        source="swebench",
        repo_dir=_placeholder(cache),       # the real workspace is in the image
        grader=grader,
        instruction=row.get("problem_statement") or None,
        gold_patch=Patch(gold) if gold else None,
        languages=frozenset(_languages(row, parser)),
        meta=meta,
        origin=None,
        workspace_env=env,
    )


# -- helpers -------------------------------------------------------------------------------


def _grade_cmd(workdir: str) -> str:
    """Apply the submitted patch the way SWE-bench does (each apply command in turn; all
    failing is an invalid run), then run the held-out eval script; all output to LOG."""
    q = f'"{SUBMISSION}"'
    apply = " || ".join(f"{c} {q}" for c in _APPLY)
    return (f"mkdir -p {EVAL_DIR}/out; cd {workdir} && {{ if [ -s {q} ]; then "
            f"{{ {apply}; }} || echo '{APPLY_FAILED}'; fi; }} > {LOG} 2>&1; "
            f"grep -q '{APPLY_FAILED}' {LOG} || /bin/bash {EVAL_DIR}/eval.sh >> {LOG} 2>&1")


def _collect_cmd(workdir: str, base: str | None) -> str:
    """An agent's work in its own environment, as the patch its harness would submit:
    every change (new files too) against the task's base commit."""
    g = "git -c safe.directory='*'"
    return (f"mkdir -p {Path(SUBMISSION).parent} && cd {workdir} && {g} add -A && "
            f"{g} diff --cached --binary {base or 'HEAD'} > {SUBMISSION}")


def _rows(path: Path) -> Iterator[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        data = json.loads(text)
        yield from (data if isinstance(data, list) else [data])
        return
    for line in text.splitlines():
        if line.strip():
            yield json.loads(line)


def _tests(raw) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            import ast
            try:
                raw = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return (raw,) if raw else ()
    return tuple(str(t) for t in raw) if isinstance(raw, list | tuple) else ()


def _workdir(script: str | None) -> str | None:
    """The repo the eval script works in: its last ``cd /abs/path`` before the tests."""
    if not script:
        return None
    head = script.split(">>>>> Start Test Output")[0]
    dirs = _CD.findall(head)
    return dirs[-1] if dirs else None


def _held_out(script: str, cache: Path | None) -> Path:
    """The eval script in its own host folder (content-addressed, so a re-check that
    didn't change it reuses its finished steps); uploaded only when grading starts."""
    root = cache or Path.home() / ".cache" / "rlgate" / "swe"
    d = root / hashlib.sha256(script.encode()).hexdigest()[:20]
    f = d / "eval.sh"
    if not f.is_file() or f.read_text(encoding="utf-8", errors="replace") != script:
        d.mkdir(parents=True, exist_ok=True)
        f.write_text(script, encoding="utf-8")
    return d


def _placeholder(cache: Path | None) -> Path:
    """An empty folder standing in for the workspace until a sandbox pulls the real one
    out of the image (the JSONL's own folder holds other rows, not this task's files)."""
    d = (cache or Path.home() / ".cache" / "rlgate" / "swe") / "workspace-in-image"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _languages(row: dict, parser: str | None) -> set[str]:
    lang = str(row.get("language") or row.get("repo_language") or "").lower()
    if lang in _LANG:
        return {_LANG[lang]}
    if parser in _PARSER_LANG:
        return {_PARSER_LANG[parser]}
    return {"python"}


def _renamed(t: Task, new_id: str) -> Task:
    import dataclasses
    return dataclasses.replace(t, id=new_id)
