"""The staged backend (workspace inside an image, grader in its own environment) end to end,
with containers faked as temp roots: absolute container paths in commands are mapped into a
per-machine directory, so the REAL transfer commands (tar, rm, find, stat) run unmodified."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from rlgate.core.grade import parse_reward, run_grader
from rlgate.core.task import EnvSpec, GraderSpec, Patch, Task
from rlgate.pipeline import audit, prepare
from rlgate.sandbox.base import ExecResult
from rlgate.sandbox.machine import Machine, changed_files
from rlgate.sandbox.staged import EnvProvider, StagedSandbox, tree_patch

_PREFIXES = ("/app", "/tests", "/logs", "/solution", "/tmp", "/results", "/grading")
_ABS = re.compile(r"(?<![\w/.$-])(" + "|".join(map(re.escape, _PREFIXES)) + r")(?=/|\s|$|'|\"|;)")


class RootMachine(Machine):
    """A 'container' whose filesystem is ``root`` on the host. Scripts inside it use $R."""

    def __init__(self, root: Path, workdir: str | None = None) -> None:
        self.root = root
        self.workdir = workdir
        self.closed = False

    def _map(self, s: str) -> str:
        return _ABS.sub(lambda m: f"{self.root}{m.group(1)}", s)

    def exec(self, cmd: str, *, cwd: str | None = None, timeout: int = 900) -> ExecResult:
        wd = cwd or self.workdir
        host_cwd = Path(self._map(wd)) if wd else self.root
        host_cwd.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["bash", "-c", self._map(cmd)], check=False, cwd=host_cwd, capture_output=True,
                           text=True, timeout=timeout, env={"R": str(self.root),
                                                             "PATH": "/usr/bin:/bin"})
        return ExecResult(p.returncode, p.stdout, p.stderr)

    def put_file(self, local: Path, remote: str) -> None:
        dst = Path(self._map(remote))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dst)

    def get_file(self, remote: str, local: Path) -> None:
        shutil.copyfile(self._map(remote), local)

    def close(self) -> None:
        self.closed = True
        shutil.rmtree(self.root, ignore_errors=True)


class FakeEnv(EnvProvider):
    """Every ``fresh()`` is a new root seeded with ``files`` (the 'image')."""

    def __init__(self, spec: EnvSpec, files: dict[str, str]) -> None:
        super().__init__(spec)
        self.files = files
        self.machines: list[RootMachine] = []
        self.provisioned = False

    def provision(self) -> None:
        self.provisioned = True

    def fresh(self) -> Machine:
        root = Path(tempfile.mkdtemp(prefix="fake-ctr-"))
        (root / "tmp").mkdir()                     # every real image has /tmp
        for path, text in self.files.items():
            p = root / path.lstrip("/")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            if path.endswith(".sh"):
                p.chmod(0o755)
        m = RootMachine(root, self.spec.workdir)
        self.machines.append(m)
        return m


BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
GOLD = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a - b\n+    return a + b\n")

# the separate grader: reward 1 iff the carried calc.py adds AND the oracle output exists
# when required; it must never see files that are not artifacts (notes.txt)
GRADER_TEST = """#!/bin/bash
mkdir -p $R/logs/verifier
if [ -e $R/app/notes.txt ]; then echo leaked; echo 0 > $R/logs/verifier/reward.txt; exit 1; fi
if grep -q 'a + b' $R/app/calc.py; then r=1; else r=0; fi
printf '{"train": %s, "eval": %s}' $r $r > $R/logs/verifier/reward.json
echo $r > $R/logs/verifier/reward.txt
"""

SOLVE = """#!/bin/bash
sed -i.bak 's/a - b/a + b/' $R/app/calc.py && rm -f $R/app/calc.py.bak
mkdir -p $R/results && echo done > $R/results/out.txt
"""


def _task(tmp_path: Path, *, separate: bool = True) -> tuple[Task, dict[str, FakeEnv]]:
    sol = tmp_path / "solution"
    sol.mkdir()
    (sol / "solve.sh").write_text(SOLVE)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(GRADER_TEST)
    ws_spec = EnvSpec(image="agent:1", workdir="/app")
    ws = FakeEnv(ws_spec, {"/app/calc.py": BUGGY, "/app/notes.txt": "agent notes\n",
                           "/app/node_modules/big/index.js": "x\n"})
    g_spec = EnvSpec(image="grader:1", workdir="/grading")
    grader = FakeEnv(g_spec, {"/tests/test.sh": GRADER_TEST} if separate else {})
    task = Task(
        id="staged", source="test", repo_dir=tmp_path, origin=str(tmp_path),
        grader=GraderSpec(
            run="chmod +x /tests/test.sh; /tests/test.sh", verdict="reward_file",
            reward_files=("/logs/verifier/reward.json", "/logs/verifier/reward.txt"),
            env=g_spec if separate else None,
            artifacts=("/app/calc.py", "/results/out.txt") if separate else (),
            inject=() if separate else ((str(tests), "/tests"),)),
        workspace_env=ws_spec, oracle=(str(sol), "solve.sh"))
    return task, {"ws": ws, "grader": grader}


def _sandbox(task: Task, envs: dict[str, FakeEnv]) -> StagedSandbox:
    sb = StagedSandbox(task, "docker")
    sb._provider = lambda spec, role: envs[role]          # type: ignore[method-assign]
    return sb


def test_workspace_is_pulled_out_of_the_image_without_dependency_trees(tmp_path):
    task, envs = _task(tmp_path)
    with _sandbox(task, envs) as sb:
        sb.build()
        ws = sb.workspace_dir
        assert (ws / "calc.py").read_text() == BUGGY and (ws / "notes.txt").exists()
        assert not (ws / "node_modules").exists()
        assert envs["grader"].provisioned
        assert all(m.closed for m in envs["ws"].machines)   # pull machine released


def test_separate_grader_sees_only_artifacts(tmp_path):
    task, envs = _task(tmp_path)
    with _sandbox(task, envs) as sb:
        sb.build()
        with sb.overlay(None) as ovl:
            assert run_grader(ovl, task).reward == 0.0
        with sb.overlay(GOLD) as ovl:
            g = run_grader(ovl, task)
        # reward.json has no "reward" key → its entries are metrics, reward.txt is THE reward
        assert g.passed and g.metrics == {"reward": 1.0, "train": 1.0, "eval": 1.0}
        # editing a non-artifact file never reaches the grader (notes.txt stays absent there)
        notes = ("--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-agent notes\n+changed\n")
        with sb.overlay(GOLD + notes) as ovl:
            assert run_grader(ovl, task).passed


def test_every_overlay_is_a_fresh_grader_environment(tmp_path):
    task, envs = _task(tmp_path)
    with _sandbox(task, envs) as sb:
        sb.build()
        for _ in range(3):
            with sb.overlay(None):
                pass
        assert len(envs["grader"].machines) == 3
        assert all(m.closed for m in envs["grader"].machines)


def test_oracle_script_is_graded_like_the_harness_and_derives_the_gold(tmp_path):
    task, envs = _task(tmp_path)
    with _sandbox(task, envs) as sb:
        sb.build()
        with sb.oracle_overlay() as ovl:
            assert run_grader(ovl, task).passed
            out = ovl.m.exec("cat /results/out.txt").stdout     # outside-workdir artifact
            assert out.strip() == "done"
        gold = sb.derive_gold()
        assert gold and "+    return a + b" in gold and "notes.txt" not in gold
        assert len(envs["ws"].machines) == 2           # pull + ONE solution run, cached


def test_prepare_swaps_in_the_real_workspace_and_derived_gold(tmp_path):
    task, envs = _task(tmp_path)
    with _sandbox(task, envs) as sb:
        sb.build()
        t = prepare(task, sb)
        assert t.repo_dir == sb.workspace_dir and t.gold_patch is not None
        assert t.meta["gold_patch"] == "derived from solution script"


def test_shared_grader_gets_tests_only_at_grade_time_and_only_changed_files(tmp_path):
    task, envs = _task(tmp_path, separate=False)
    with _sandbox(task, envs) as sb:
        sb.build()
        with sb.overlay(GOLD) as ovl:
            assert ovl.m.exec("test -e /tests/test.sh").exit_code != 0   # held out so far
            assert ovl.m.exec("cat /app/notes.txt").stdout == "agent notes\n"
            g = run_grader(ovl, task)
        # shared mode: the whole workspace is there (notes.txt included) → the grader's
        # leak check fires; what matters here is that tests arrived and graded
        assert g.reward == 0.0 and "leaked" in g.stdout


def test_full_audit_runs_on_the_staged_backend(tmp_path):
    task, envs = _task(tmp_path)
    sb = _sandbox(task, envs)
    result = audit(task, sandbox=sb, k=2, only={"grader_strength.revert_probe"})
    sb.close()
    assert result.baseline_sane, [f.title for f in result.findings]


def test_unpatched_oracle_failure_is_reported_with_its_output(tmp_path):
    task, envs = _task(tmp_path)
    (Path(task.oracle[0]) / "solve.sh").write_text("#!/bin/bash\necho boom; exit 3\n")
    with _sandbox(task, envs) as sb:
        sb.build()
        with pytest.raises(Exception, match="(?s)exited 3.*boom"):
            sb.oracle_overlay()


def test_local_backend_is_refused_with_a_reason(tmp_path):
    from rlgate.sandbox.select import BackendUnavailable, build_sandbox
    task, _ = _task(tmp_path)
    with pytest.raises(BackendUnavailable, match="container images"):
        build_sandbox(task, prefer="local")


# -- helpers --------------------------------------------------------------------------


def test_changed_files_and_tree_patch_roundtrip(tmp_path):
    from rlgate.sandbox._patch import apply_zero_fuzz
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        (d / "pkg").mkdir(parents=True)
    (a / "pkg" / "x.py").write_text("1\n")
    (b / "pkg" / "x.py").write_text("2\n")
    (a / "gone.txt").write_text("bye\n")
    (b / "new.bin").write_bytes(b"\x00\x01binary\xff")
    written, deleted = changed_files(a, b)
    assert written == ["new.bin", "pkg/x.py"] and deleted == ["gone.txt"]
    patch = tree_patch(a, b)
    c = tmp_path / "c"
    shutil.copytree(a, c)
    apply_zero_fuzz(c, patch)
    assert changed_files(b, c) == ([], [])
    assert tree_patch(a, a) is None


@pytest.mark.parametrize("text, json_file, want", [
    ("1.0\n", False, (1.0, {"reward": 1.0})),
    ('{"reward": 0.5, "x": 1}', True, (0.5, {"reward": 0.5, "x": 1.0})),
    ('{"only": 0.25}', True, (0.25, {"only": 0.25})),
    ('{"reward": 1, "flag": true}', True, (1.0, {"reward": 1.0, "flag": 1.0})),
])
def test_parse_reward(text, json_file, want):
    assert parse_reward(text, json_file=json_file) == want


@pytest.mark.parametrize("text, json_file", [("", False), ("nan", False), ("abc", False),
                                             ("[1", True), ('{"r": "NaN"}', True)])
def test_parse_reward_rejects(text, json_file):
    with pytest.raises(ValueError):
        parse_reward(text, json_file=json_file)


def test_missing_reward_file_scores_zero_and_says_why(tmp_path):
    task, envs = _task(tmp_path)
    task = Task(**{**task.__dict__, "grader": GraderSpec(
        **{**task.grader.__dict__, "run": "true"})})
    with _sandbox(task, envs) as sb:
        sb.build()
        with sb.overlay(GOLD) as ovl:
            g = run_grader(ovl, task)
    assert g.reward == 0.0 and "no reward file" in (g.reward_error or "")


def test_gold_patch_object(tmp_path):
    assert Patch(GOLD).patchset[0].path == "calc.py"
