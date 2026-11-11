"""L1 verifier guard — "the verifier command cannot manufacture success, suppress failures,
or replace behavioural tests with superficial checks". File-only: the grading command and
every shell script it runs (Harbor's ``tests/test.sh``) are read for:

  - suppressed failures: a test run followed by ``|| true`` / ``|| :`` / ``|| exit 0``
    (judged against how the grade is decided: harmless when the reward comes from a report);
  - selection the agent can steer: ``--lf`` / ``--last-failed`` / ``--sw`` (they read
    ``.pytest_cache``), ``--collect-only`` / ``--co`` / ``--setup-only`` / ``--setup-plan``
    (no test body runs), ``--suppress-tests-failed-exit-code``;
  - manufactured success: a reward of 1 written unconditionally (outside any if/&&/||), or a
    script that ends in ``exit 0`` when the exit code is the verdict;
  - superficial checks: a script that never runs a test runner or program, only checks that
    files exist or contain a string.

Each is a concrete line in a file, but whether it matters depends on the rest of the
script, so the finding needs your judgment. Runs with --quick.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from rlgate.core import rules
from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task

RUNNERS = re.compile(
    r"\b(pytest|py\.test|unittest|nosetests|tox|go\s+test|cargo\s+(test|nextest)|"
    r"npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|npx\s+\w+|jest|vitest|mocha|ava|"
    r"mvn\s+\S*\s*test|gradle\w*\s+test|dotnet\s+test|ctest|make\s+(test|check)|bats|"
    r"rspec|phpunit|prove|deno\s+test|bun\s+test)\b(?![.\w-])")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
PROGRAMS = re.compile(r"\b(python3?|node|ruby|perl|java|bash|sh|\./)\S*\s+\S+|\$PYTHON\w*")
IGNORED = re.compile(r"\|\|\s*(true|:|exit\s+0)\b")
STEERED = re.compile(r"(?<!\S)(--lf|--last-failed|--sw|--stepwise|--co|--collect-only|"
                     r"--setup-only|--setup-plan|--suppress-tests-failed-exit-code)(?!\S)")
REWARD_ONE = re.compile(r"\becho\s+['\"]?(1|1\.0|\{\s*\"reward\"\s*:\s*1(\.0)?\s*\})['\"]?\s*"
                        r">+\s*\S*reward")
SUPERFICIAL = re.compile(r"(\[\s+-[efsd]\s|\btest\s+-[efsd]\s|\bgrep\s+-q)")
_OPEN = re.compile(r"^(if|while|until|for|case)\b|\{\s*$|\(\s*$")
_CLOSE = re.compile(r"^(fi|done|esac)\b|^\}|^\)")
_SCRIPT = re.compile(r"[\w./-]+\.(sh|bash)\b")


def scripts(task: Task) -> list[tuple[str, str]]:
    """(display name, text) for the grading command and each shell script it names that
    can be found among the task's files."""
    out = [("grader command", task.grader.run)]
    seen: set[str] = set()
    for m in _SCRIPT.finditer(task.grader.run):
        name = Path(m.group(0)).name
        if name in seen:
            continue
        for root, prefix in task.test_roots:
            hits = [p for p in root.rglob(name) if p.is_file()
                    and ".git" not in p.parts and "node_modules" not in p.parts]
            if hits:
                seen.add(name)
                try:
                    out.append((prefix + hits[0].relative_to(root).as_posix(),
                                hits[0].read_text(errors="ignore")))
                except OSError:
                    pass
                break
    return out


def _logical(text: str) -> list[tuple[int, str]]:
    """(1-based line, text) with continuation lines folded in and comments dropped."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not buf:
            start = n
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        full = (buf + line).strip()
        buf = ""
        if full and not full.startswith("#"):
            out.append((start, full))
    return out


@register
class VerifierCommand(Probe):
    meta = ProbeMeta(
        id="forgery.verifier_command",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        doc="The grading command can report success without the tests passing.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        findings: list[Finding] = []
        exit_verdict = task.grader.verdict in ("exit_code", "both")
        for name, text in scripts(task):
            lines = _logical(text)
            depth = 0
            runs_something = False
            last = ""
            for i, (n, line) in enumerate(lines):
                if _CLOSE.match(line):
                    depth = max(0, depth - 1)
                loc = name if name == "grader command" else f"{name}:{n}"
                bare = _QUOTED.sub("''", line)         # `-name 'pytest.ini'` runs nothing
                is_test = bool(RUNNERS.search(bare))
                runs_something = runs_something or is_test or bool(PROGRAMS.search(line))
                if is_test and IGNORED.search(line):
                    nxt = lines[i + 1][1] if i + 1 < len(lines) else ""
                    if "$?" in nxt:
                        how = "the `$?` checked on the next line is then always 0"
                    elif exit_verdict:
                        how = "the exit code is the verdict, so every run counts as a pass"
                    else:
                        how = ("the reward must come from somewhere else (a report); check "
                               "it can't be empty or stale")
                    findings.append(self._f(loc, f"`{_short(line)}` ignores the test run's "
                                                 f"failure: {how}"))
                m = STEERED.search(line)
                if is_test and m:
                    findings.append(self._f(loc, f"`{_short(line)}` uses {m.group(1)}: "
                                                 + _steer_why(m.group(1))))
                if depth == 0 and REWARD_ONE.search(line) and "&&" not in line \
                        and "||" not in line and not _OPEN.match(line) \
                        and not re.search(r"\bthen\b", line):
                    findings.append(self._f(loc, f"`{_short(line)}` writes a full reward "
                                                 "outside any condition"))
                if _OPEN.match(line) and not re.search(r"\b(fi|done|esac)\s*;?\s*$", line):
                    depth += 1
                last = line
            if name != "grader command" and exit_verdict and re.fullmatch(r"exit\s+0", last):
                findings.append(self._f(f"{name}:{lines[-1][0]}",
                                        "the script ends in `exit 0` and the exit code is the "
                                        "verdict, so it passes whatever the tests did"))
            if name != "grader command" and lines and not runs_something and \
                    any(SUPERFICIAL.search(ln) for _, ln in lines):
                findings.append(self._f(name, "the script runs no test or program; it only "
                                              "checks that files exist or contain a string"))
        return _dedupe(findings)

    def _f(self, loc: str, detail: str) -> Finding:
        rule = rules.get("RG208")
        path, _, line = loc.partition(":")
        replay = (f"sed -n '{line}p' {shlex.quote(path)}" if line.isdigit() else None)
        return Finding(probe_id=self.meta.id, rule_id="RG208", title=rule.title,
                       severity=rule.severity, verdict=VerdictTier.REVIEW, location=loc,
                       evidence=Evidence(kind="code_line", detail=detail, replay=replay),
                       fix_hint=rule.fix)


def _steer_why(flag: str) -> str:
    if flag in ("--lf", "--last-failed", "--sw", "--stepwise"):
        return ("which tests run is read from .pytest_cache, which the agent can write")
    if flag == "--suppress-tests-failed-exit-code":
        return "failing tests still exit 0"
    return "no test body runs, so nothing can fail"


def _short(line: str, n: int = 90) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def _dedupe(fs: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    out = []
    for f in fs:
        key = (f.location or "", f.evidence.detail)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
