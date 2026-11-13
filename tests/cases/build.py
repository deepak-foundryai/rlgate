"""Test cases for every check, built from real tasks under data/.

Each case is a real Terminal-Bench task:
  - natural: used as-is, because the problem (or its absence) occurs in it already;
  - planted: copied to data/cases/<name>/ with exactly ONE change that introduces a defect.

data/ is not in git (it holds the task corpora), so this file is the tracked source of truth:
`python -m tests.cases.build` (or the test session) recreates data/cases/ from data/.

Source: data/terminal-bench/, harbor-framework/terminal-bench @ 9a318be6 (Apache-2.0).

Maintainers may add cases on task banks that can't be distributed in an untracked
``tests/cases/private.py`` (``CASES = [Case(...), ...]``, sources under data/). They run
when present; rules only they cover are listed in NOT_COVERED otherwise.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
TB = DATA / "terminal-bench"
CASES = DATA / "cases"


@dataclass(frozen=True)
class Case:
    name: str
    source: Path                        # a real task directory
    expect: frozenset[str]              # rules that MUST be found
    absent: frozenset[str] = frozenset()    # rules that must NOT be found
    plant: Callable[[Path], None] | None = None   # the one change (None = natural)
    change: str = ""                    # what the change is, for humans
    live: bool = False                  # needs a full run (Docker / Daytona)
    limited: bool = False               # rlgate can't run it fully: quick only, with a reason
    note: str = ""

    @property
    def path(self) -> Path:
        return CASES / self.name if self.plant else self.source


# -- planting helpers: keep mtimes like the neighbours, so only the intended rule fires ----

def _like(target: Path, ref: Path) -> None:
    st = ref.stat()
    os.utime(target, (st.st_atime, st.st_mtime))


def _write(task: Path, rel: str, text: str | bytes, like: str) -> None:
    p = task / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode())
    _like(p, task / like)
    _like(p.parent, task / like)


def _edit(task: Path, rel: str, fn: Callable[[str], str]) -> None:
    p = task / rel
    st = p.stat()
    p.write_text(fn(p.read_text()))
    os.utime(p, (st.st_atime, st.st_mtime))


def _remove(task: Path, rel: str) -> None:
    p = task / rel
    parent_st = p.parent.stat()
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    os.utime(p.parent, (parent_st.st_atime, parent_st.st_mtime))


VIG = TB / "tasks" / "interleaved-vigenere"


def _ignored_failure(task: Path) -> None:
    _edit(task, "tests/test.sh", lambda t: t.replace(
        "/tests/test_outputs.py -rA\n", "/tests/test_outputs.py -rA || true\n", 1))


def _orig(t: Path) -> None:
    src = (t / "environment/generate_sample.py").read_text()
    _write(t, "environment/generate_sample.py.orig", src + "\n# reference version\n",
           like="environment/generate_sample.py")


def _answer_file(t: Path) -> None:
    _write(t, "environment/expected_output.txt", (t / "solution/cracker.py").read_text(),
           like="environment/generate_sample.py")


def _orphan_pyc(t: Path) -> None:
    _write(t, "environment/__pycache__/cracker.cpython-313.pyc",
           b"\xf3\r\r\n" + b"\0" * 12 + b"compiled reference solution",
           like="environment/generate_sample.py")


def _call_count(t: Path) -> None:
    _edit(t, "tests/test_outputs.py", lambda s: s + '''

def test_runs_the_cracker_once():
    from unittest import mock
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="")
        subprocess.run(["true"])
    assert run.call_count == 1
''')


def _no_solution(t: Path) -> None:
    _remove(t, "solution/solve.sh")


def _no_reward(t: Path) -> None:
    for f in (t / "tests").rglob("*"):
        if f.is_file() and f.suffix in (".sh", ".py", ""):
            _edit(t, str(f.relative_to(t)),
                  lambda s: s.replace("/logs/verifier/reward", "/logs/verifier/score")
                  .replace("reward", "score"))


def _no_artifacts(t: Path) -> None:
    _edit(t, "task.toml", lambda s: "\n".join(
        ln for ln in s.splitlines() if not ln.startswith("artifacts")) + "\n")


def _compose_no_main(t: Path) -> None:
    _edit(t, "environment/docker-compose.yaml",
          lambda s: s.replace("\n  main:\n", "\n  app:\n", 1))


def _unpinned_and_ranges(t: Path) -> None:
    def df(s: str) -> str:
        lines = s.splitlines()
        i = next(i for i, ln in enumerate(lines) if ln.startswith("FROM "))
        lines[i] = "FROM python"
        lines.insert(i + 1, "COPY . /app")
        lines.append("RUN pip install 'numpy>=1.26'")
        return "\n".join(lines) + "\n"
    _edit(t, "environment/Dockerfile", df)
    # the broad copy matters because the build context holds a copy of the reference
    _write(t, "environment/helpers.py", (t / "solution/cracker.py").read_text(),
           like="environment/generate_sample.py")


def _nproc(t: Path) -> None:
    _edit(t, "environment/Dockerfile", lambda s: s.rstrip("\n")
          + "\nRUN make -j$(nproc) -C /tmp -n || true\n")


def _gold_fails(t: Path) -> None:
    _edit(t, "solution/solve.sh", lambda s: "#!/bin/bash\n# planted: does nothing\ntrue\n")


def _nop_passes(t: Path) -> None:
    _edit(t, "tests/test.sh", lambda s: "#!/bin/bash\nmkdir -p /logs/verifier\n"
          "echo 1 > /logs/verifier/reward.txt\n")


def _flaky(t: Path) -> None:
    _edit(t, "tests/test.sh", lambda s: "#!/bin/bash\nmkdir -p /logs/verifier\n"
          "echo $((RANDOM % 2)) > /logs/verifier/reward.txt\n")


def _layer_order(t: Path) -> None:
    _edit(t, "environment/Dockerfile", lambda s: s.replace(
        "WORKDIR /app\n", "WORKDIR /app\nCOPY generate_sample.py /app/generate_sample.py\n", 1))


def _grader_download(t: Path) -> None:
    _edit(t, "tests/test.sh", lambda s: s.replace(
        "cd /tests\n", "cd /tests\ncurl -sSf https://example.com/words.txt -o /tmp/w.txt\n", 1))


def _no_limits(t: Path) -> None:
    _edit(t, "task.toml", lambda s: "\n".join(
        ln for ln in s.splitlines() if not ln.startswith(("cpus", "memory_mb"))) + "\n")


def _no_internet(t: Path) -> None:
    _edit(t, "task.toml", lambda s: s.replace("[environment]\n",
                                              "[environment]\nallow_internet = false\n", 1))


def _mutable_expected(t: Path) -> None:
    """The verifier reads an expected output that ships in the agent's workspace (and is
    carried back to the verifier as an artifact)."""
    _write(t, "environment/fixtures/known_plaintext.txt", "attack at dawn\n",
           like="environment/generate_sample.py")
    _edit(t, "task.toml", lambda s: s.replace(
        'artifacts = ["/app/cracker.py", "/app/requirements.txt"]',
        'artifacts = ["/app/cracker.py", "/app/requirements.txt", "/app/fixtures"]'))
    _edit(t, "tests/test_outputs.py", lambda s: s + '''

def test_matches_the_expected_output():
    expected = open("/app/fixtures/known_plaintext.txt").read()
    assert expected
''')


def _build_fails(t: Path) -> None:
    _edit(t, "environment/Dockerfile", lambda s: s + "RUN false   # planted: the build fails\n")


def _solution_in_image(t: Path) -> None:
    _write(t, "environment/cracker_ref.py", (t / "solution/cracker.py").read_text(),
           like="environment/generate_sample.py")
    _edit(t, "environment/Dockerfile", lambda s: s + "COPY cracker_ref.py /opt/cracker_ref.py\n")


def _fix_breaks_a_test(t: Path) -> None:
    _edit(t, "tests/test_outputs.py", lambda s: s + '''

def test_no_cracker_yet():
    import os
    assert not os.path.exists("/app/cracker.py")
''')


def _tight_timeout(t: Path) -> None:
    _edit(t, "task.toml", lambda s: s.replace("timeout_sec = 900.0", "timeout_sec = 12.0", 1))


F = frozenset
ALL: list[Case] = [
    # -- natural: occurs in real tasks as shipped ------------------------------------------
    Case("rg004-shared-runtime", TB / "legacy/regex-log", F({"RG004"}), F({"RG605"}),
         note="legacy Terminal-Bench format: tests run inside the agent's container"),
    Case("ok-prebuilt-unpinned-recipe", TB / "tasks/legacy-utility-triage", F(), F({"RG602"}),
         note="the task runs a prebuilt image: an unpinned FROM in its recipe never runs"),
    Case("rg603-nproc", VIG, F({"RG603"}), plant=_nproc,
         change="a RUN line using $(nproc) in environment/Dockerfile"),
    Case("ok-prebuilt-recipe", TB / "legacy/compile-compcert", F(), F({"RG603"}),
         note="the task runs a prebuilt image: the nproc in the recipe beside it never runs"),
    Case("ok-prebuilt-range-recipe", TB / "legacy/video-processing", F(), F({"RG604"}),
         note="the task runs a prebuilt image: a version range in its recipe never runs"),
    # -- natural controls: must NOT be flagged ---------------------------------------------
    Case("ok-reward-written-by-helper", TB / "tasks/cargo-flight-dispatch", F(),
         F({"RG605"}), note="test.sh execs verify.py, which writes the reward"),
    Case("limit-sidecar-artifacts", TB / "tasks/ctr-optimization", F(), F({"RG605"}),
         limited=True, note="artifacts collected from service 'api': valid Harbor, but "
                            "rlgate can't reproduce it yet, so quick only"),
    # -- planted (one change each) ---------------------------------------------------------
    Case("rg303-patch-leftover", VIG, F({"RG303"}), plant=_orig,
         change="environment/generate_sample.py.orig added"),
    Case("rg304-answer-file", VIG, F({"RG304"}), plant=_answer_file,
         change="environment/expected_output.txt holding solution/cracker.py"),
    Case("rg302-orphan-bytecode", VIG, F({"RG302"}), plant=_orphan_pyc,
         change="environment/__pycache__/cracker.cpython-313.pyc with no cracker.py"),
    Case("rg404-call-count", VIG, F({"RG404"}), plant=_call_count,
         change="a test asserting run.call_count == 1"),
    Case("rg605-no-solution", VIG, F({"RG605"}), plant=_no_solution,
         change="solution/solve.sh removed"),
    Case("rg605-no-reward", VIG, F({"RG605"}), plant=_no_reward,
         change="every mention of the reward file in tests/ renamed to 'score'"),
    Case("rg605-no-artifacts", VIG, F({"RG605"}), plant=_no_artifacts,
         change="artifacts line removed from task.toml (separate verifier)"),
    Case("rg605-compose-no-main", TB / "tasks/cumulative-layout-shift", F({"RG605"}),
         plant=_compose_no_main, change="compose service 'main' renamed to 'app'"),
    Case("rg601-602-604-dockerfile", VIG, F({"RG601", "RG602", "RG604"}),
         plant=_unpinned_and_ranges,
         change="FROM python (no tag), COPY . /app with a copy of the reference in the "
                "build context, pip install 'numpy>=1.26'"),
    Case("rg606-layer-order", VIG, F({"RG606"}), F({"RG601"}), plant=_layer_order,
         change="COPY generate_sample.py before the apt-get install layer"),
    Case("rg607-grader-download", VIG, F({"RG607"}), plant=_grader_download,
         change="tests/test.sh downloads a file with curl before running the tests"),
    Case("rg012-no-limits", VIG, F({"RG012"}), plant=_no_limits,
         change="cpus and memory_mb removed from task.toml"),
    Case("rg307-open-internet", VIG, F({"RG307"}), F({"RG012", "RG607"}),
         note="allow_internet is not set, so the agent has open internet"),
    Case("ok-no-internet", VIG, F(), F({"RG307"}), plant=_no_internet,
         change="allow_internet = false in task.toml"),
    Case("rg213-mutable-expected-output", VIG, F({"RG213"}), plant=_mutable_expected,
         change="the verifier reads /app/fixtures/known_plaintext.txt, shipped to the agent "
                "and carried back as an artifact"),
    Case("rg208-ignored-test-failure", VIG, F({"RG208"}), plant=_ignored_failure,
         change="tests/test.sh: `|| true` after the pytest run whose $? sets the reward"),
    Case("ok-verifier-command", VIG, F(), F({"RG208"}),
         note="the reward follows pytest's exit status, under an if"),
    # -- planted, full run only (Docker / Daytona) ------------------------------------------
    Case("rg608-build-fails", VIG, F({"RG608"}), plant=_build_fails, live=True,
         change="RUN false appended to environment/Dockerfile"),
    Case("rg306-solution-in-image", VIG, F({"RG306"}), plant=_solution_in_image, live=True,
         change="a copy of solution/cracker.py baked into the agent image at /opt"),
    Case("rg007-fix-breaks-a-test", VIG, F({"RG007"}), plant=_fix_breaks_a_test, live=True,
         change="a test that passes only while /app/cracker.py doesn't exist"),
    Case("rg011-tight-timeout", VIG, F({"RG011"}), plant=_tight_timeout, live=True,
         change="[verifier] timeout_sec 900 -> 12"),
    Case("rg008-no-pass-to-pass", VIG, F({"RG008"}), live=True,
         note="every graded test fails until cracker.py exists, so none guards regressions"),
    Case("rg010-shared-runtime-carryover", TB / "legacy/regex-log", F({"RG010"}), live=True,
         note="legacy shared runtime: /tmp and processes survive into grading"),
    Case("rg001-gold-fails", VIG, F({"RG001"}), plant=_gold_fails, live=True,
         change="solution/solve.sh replaced with a no-op"),
    Case("rg002-nop-passes", VIG, F({"RG002"}), plant=_nop_passes, live=True,
         change="tests/test.sh always writes reward 1"),
    Case("rg003-flaky", VIG, F({"RG003"}), plant=_flaky, live=True,
         change="tests/test.sh writes a random reward"),
]

try:                       # untracked: cases on task banks that can't be distributed
    from tests.cases.private import CASES as _PRIVATE
except ImportError:
    _PRIVATE = []
_PUBLIC = set().union(*(c.expect for c in ALL))
ALL += _PRIVATE
BY_NAME = {c.name: c for c in ALL}
# Rules no case covers yet, and why (kept honest; tests check this list stays current).
NOT_COVERED = {
    "RG403": "no natural case: the one it had was a false hit (fixed in 0.1.7); tested on "
             "a fixture in tests/probes/test_hygiene_and_brittle.py",
    "RG101": "full run on a task with a gold patch whose tests miss a hunk: none found yet",
    "RG103": "full run (mutation survives): none found yet",
    "RG201": "full run: needs a task whose verifier lets a planted conftest.py load",
    "RG210": "needs a grader that reads a report file; Harbor graders write a reward",
    "RG212": "full run: held-out tests readable by the agent",
    "RG301": "full run: fix recoverable from git objects in the image",
    "RG401": "full run: varying an incidental string breaks a test",
    "RG006": "needs a grader whose reward moves while no reported test does: none found yet",
    **{r: "proven on a local fixture (tests/probes/test_l1.py); no real task has the hole "
          "and a planted one needs a full run" for r in
       ("RG102", "RG104", "RG202", "RG203", "RG204", "RG206", "RG207", "RG214")},
    "RG205": "needs Docker or Daytona: the plants write to site-packages (unit-tested in "
             "tests/probes/test_l1.py)",
    "RG009": "needs a reference solution whose effect lives outside the workspace: none "
             "found yet",
    **{f"RG50{i}": "Harbor tasks declare no scoring spec, so reward-shape checks don't apply"
       for i in range(1, 8)},
    **{r: "LLM review (--deep-review): needs a model and a key, so cases here run offline; the "
          "judge flow is tested with a scripted model in tests/review/test_l2.py" for r in
       ("RG105", "RG106", "RG107", "RG108", "RG109", "RG405", "RG406", "RG407", "RG701",
        "RG702", "RG703", "RG704")},
    **{r: "L3 agent (--deep-review, full run): needs a model and a key; proven on a local fixture "
          "with a scripted agent in tests/review/test_l3.py" for r in ("RG110", "RG408")},
    **{r: "L4 hack agent (--deep-review, full run on docker or daytona): needs a model and a key; "
          "proven on a fixture with a scripted agent in tests/review/test_l4.py" for r in
       ("RG111", "RG113", "RG215", "RG308")},
    **{r: "rubric-graded tasks: none in the case banks (all are code tasks); tested on "
          "fixtures in tests/review/test_rubric.py" for r in
       ("RG112", "RG409", "RG410", "RG411")},
}


# rules the private cases cover; without them, say so instead of failing the coverage check
if not _PRIVATE:
    NOT_COVERED.update({r: "covered by maintainers' cases on a task bank that can't be "
                           "distributed (tests/cases/private.py)"
                        for r in {"RG211", "RG305", "RG402", "RG403", "RG601"} - _PUBLIC})


def available() -> bool:
    return TB.is_dir()


def build(cases: list[Case] | None = None) -> list[Path]:
    """(Re)create every planted case under data/cases/. Natural cases need no build."""
    out = []
    for c in cases or ALL:
        if c.plant is None:
            continue
        dest = CASES / c.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(c.source, dest, symlinks=True)       # copy2: mtimes kept
        c.plant(dest)
        out.append(dest)
    _manifest()
    return out


def _manifest() -> None:
    lines = ["# rlgate test cases", "", "Generated by tests/cases/build.py: do not edit.", "",
             "| Case | Task | Change | Expect | Must not find | Run |",
             "| --- | --- | --- | --- | --- | --- |"]
    for c in ALL:
        src = c.source.relative_to(DATA).as_posix()
        lines.append(f"| {c.name} | {src} | {c.change or '(as shipped)'} | "
                     f"{', '.join(sorted(c.expect)) or '-'} | "
                     f"{', '.join(sorted(c.absent)) or '-'} | "
                     f"{'full' if c.live else 'quick'} |")
    lines += ["", "Not covered yet:", ""] + [f"- {r}: {why}" for r, why in NOT_COVERED.items()]
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / "CASES.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    for p in build():
        print("built", p.relative_to(REPO))
    print("wrote", (CASES / "CASES.md").relative_to(REPO))
