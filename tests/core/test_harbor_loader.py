"""Native Harbor-format loading: task.toml semantics mapped onto generic Task fields."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from rlgate.ingest import harbor, spec
from rlgate.ingest.bank import detect_layout, load_bank
from rlgate.runtime import HostCapabilities, precheck
from rlgate.runtime.spec import RuntimeKind
from rlgate.sandbox.staged import _compose_base, _compose_override

PATCH = "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a\n+b\n"


def _task(root: Path, name: str = "t1", *, toml: str = "", env_df: str | None = None,
          env_compose: str | None = None, tests_df: str | None = None,
          tests_compose: str | None = None, solve: str | None = "#!/bin/bash\ntrue\n",
          files: dict[str, str] | None = None) -> Path:
    d = root / name
    (d / "environment").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "instruction.md").write_text("do it\n")
    (d / "tests" / "test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
    (d / "task.toml").write_text(toml)
    if env_df is not None:
        (d / "environment" / "Dockerfile").write_text(env_df)
    if env_compose is not None:
        (d / "environment" / "docker-compose.yaml").write_text(env_compose)
    if tests_df is not None:
        (d / "tests" / "Dockerfile").write_text(tests_df)
    if tests_compose is not None:
        (d / "tests" / "docker-compose.yaml").write_text(tests_compose)
    if solve is not None:
        (d / "solution").mkdir()
        (d / "solution" / "solve.sh").write_text(solve)
    for rel, text in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


def test_shared_mode_uploads_tests_and_reads_the_reward_file(tmp_path):
    d = _task(tmp_path, toml='[verifier]\ntimeout_sec = 120\n[verifier.env]\nK = "v"\n',
              env_df="FROM python:3.12\nWORKDIR /app\n")
    t = harbor.load(d)
    assert t.meta["verifier_mode"] == "shared" and t.grader.env is None
    assert t.grader.inject == ((str(d / "tests"), "/tests"),)
    assert t.grader.verdict == "reward_file" and t.grader.timeout_s == 120
    assert t.grader.reward_files[0].endswith("reward.json")
    assert t.grader.env_vars == (("K", "v"),)
    assert t.workspace_env.workdir == "/app" and t.repo_dir == d / "environment"
    assert t.oracle == (str(d / "solution"), "solve.sh") and t.gold_patch is None
    assert t.staged and "unsupported" not in t.meta


def test_separate_mode_with_a_tests_dockerfile_bakes_the_tests_in(tmp_path):
    d = _task(tmp_path, toml='artifacts = ["/app/src/", {source = "/out/r.json"}]\n'
                             '[verifier]\nenvironment_mode = "separate"\n',
              env_df="FROM x\nWORKDIR /app\n", tests_df="FROM y\nWORKDIR /grading\n")
    t = harbor.load(d)
    assert t.grader.env.dockerfile == d / "tests" / "Dockerfile"
    assert t.grader.env.workdir == "/grading" and t.grader.inject == ()
    assert t.grader.artifacts == ("/app/src", "/out/r.json", "/logs/artifacts")


def test_separate_mode_without_a_tests_env_reuses_the_agent_env_and_uploads_tests(tmp_path):
    d = _task(tmp_path, toml='artifacts = ["/app/x"]\n[verifier]\nenvironment_mode="separate"\n',
              env_df="FROM x\nWORKDIR /app\n")
    t = harbor.load(d)
    assert t.grader.env == t.workspace_env and t.grader.inject


def test_verifier_docker_image_implies_separate(tmp_path):
    d = _task(tmp_path, toml='artifacts = ["/app/x"]\n'
                             '[verifier.environment]\ndocker_image = "ghcr.io/v:1"\n',
              env_df="FROM x\nWORKDIR /app\n")
    t = harbor.load(d)
    assert t.meta["verifier_mode"] == "separate" and t.grader.env.image == "ghcr.io/v:1"
    assert t.grader.inject == ()


def test_prebuilt_image_is_preferred_and_resources_parse(tmp_path):
    d = _task(tmp_path, toml='[environment]\ndocker_image = "reg/img:2"\ncpus = 2\n'
                             'memory = "4G"\nstorage_mb = 10240\nallow_internet = false\n',
              env_df="FROM base\nWORKDIR /srv\nWORKDIR app\n")
    t = harbor.load(d)
    e = t.workspace_env
    assert e.image == "reg/img:2" and e.dockerfile is None and e.workdir == "/srv/app"
    assert (e.cpus, e.memory_mb, e.storage_mb, e.network) == (2.0, 4096, 10240, False)


def test_compose_main_without_build_uses_the_harness_base(tmp_path):
    d = _task(tmp_path, env_df="FROM x\nENV ROOT=/opt/w\nWORKDIR $ROOT\n",
              env_compose="services:\n  main:\n    depends_on: [db]\n  db:\n    image: pg\n")
    t = harbor.load(d)
    assert t.workspace_env.compose is not None and t.workspace_env.workdir == "/opt/w"
    base = _compose_base(t.workspace_env)
    assert base["services"]["main"]["build"] == {"context": "."}
    over = _compose_override(t.workspace_env.compose, "k", "main")
    assert over["services"] == {"main": {"image": "rlgate-k-main:latest"}}


def test_compose_override_tags_every_built_service(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump({"services": {
        "main": {"build": {"context": "."}, "working_dir": "/grading"},
        "appsvc": {"build": {"dockerfile": "Dockerfile.svc"}},
        "db": {"image": "postgres:15"}}}))
    over = _compose_override(f, "k", "main")["services"]
    assert set(over) == {"main", "appsvc"}


def test_gold_patch_only_when_solve_sh_applies_it(tmp_path):
    a = _task(tmp_path, "a", env_df="FROM x\nWORKDIR /app\n",
              solve="#!/bin/bash\ncd /app && patch -p1 < /solution/patch.diff\n",
              files={"solution/patch.diff": PATCH})
    b = _task(tmp_path, "b", env_df="FROM x\nWORKDIR /app\n",
              solve="#!/bin/bash\npython /solution/solve.py\n",
              files={"solution/unrelated.patch": PATCH})
    assert harbor.load(a).gold_patch.text == PATCH
    assert harbor.load(b).gold_patch is None and harbor.load(b).oracle is not None


def test_unsupported_shapes_are_flagged_not_guessed(tmp_path):
    d = _task(tmp_path, toml='artifacts = [{source = "/r.json", service = "loadgen"}]\n'
                             '[verifier]\nenvironment_mode = "separate"\n'
                             '[[steps]]\nname = "one"\n',
              env_df="FROM x\nWORKDIR /app\n")
    t = harbor.load(d)
    reasons = " ".join(t.meta["unsupported"])
    assert "multi-step" in reasons and "loadgen" in reasons
    plan = precheck(t, HostCapabilities(daytona=True))
    assert not plan.usable and "loadgen" in plan.reason


def test_env_templates_resolve_from_the_host(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_KEY", "sk-1")
    d = _task(tmp_path, toml='[verifier.env]\nA = "${JUDGE_KEY}"\nB = "${NOPE:-dflt}"\n',
              env_df="FROM x\nWORKDIR /app\n")
    assert dict(harbor.load(d).grader.env_vars) == {"A": "sk-1", "B": "dflt"}


def test_dispatch_and_bank_detection(tmp_path):
    _task(tmp_path / "bank" / "tasks", "x", env_df="FROM a\nWORKDIR /app\n")
    _task(tmp_path / "bank" / "tasks", "y", env_df="FROM a\nWORKDIR /app\n",
          env_compose="services:\n  main: {}\n")
    bank = tmp_path / "bank"
    assert detect_layout(bank) == "harbor"
    tasks = load_bank(bank, preset="harbor")
    assert [t.id for t in tasks] == ["x", "y"]
    assert spec.load(bank / "tasks" / "x").source == "harbor"            # dir
    assert spec.load(bank / "tasks" / "x" / "task.toml").id == "x"      # the toml itself
    kinds = {t.id: precheck(t, HostCapabilities(daytona=True)) for t in tasks}
    assert kinds["y"].kind is RuntimeKind.COMPOSE and kinds["y"].usable
    assert "Docker-in-Docker" in kinds["y"].reason


@pytest.mark.parametrize("df, want", [
    ("FROM a\n", None),
    ("FROM a\nWORKDIR /x\nFROM b\nWORKDIR /y\n", "/y"),
    ("FROM a AS b\nWORKDIR /x\nFROM c\n", None),        # a later stage resets it
    ('FROM a\nARG D=/d\nWORKDIR "${D}/sub"\n', "/d/sub"),
    ("FROM a\nWORKDIR /a \\\n  \n", "/a"),
])
def test_dockerfile_workdir(tmp_path, df, want):
    (tmp_path / "Dockerfile").write_text(df)
    assert harbor.dockerfile_workdir(tmp_path / "Dockerfile") == want


_BANK = os.environ.get("RLGATE_TEST_HARBOR_BANK")


@pytest.mark.skipif(not _BANK, reason="set RLGATE_TEST_HARBOR_BANK to a folder of Harbor tasks")
def test_a_whole_harbor_bank_loads():
    tasks = list(harbor.load_bank(Path(_BANK or ".")))
    assert tasks
    for t in tasks:
        assert "unsupported" not in t.meta, (t.id, t.meta.get("unsupported"))
        assert t.grader.env is not None
