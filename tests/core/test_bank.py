"""Declarative bank mapping: dir-per-task and row-per-task layouts, bundled presets, a custom
mapping for a novel layout, and task.rlgate.yaml discovery."""

from __future__ import annotations

import json
from pathlib import Path

from rlgate.core.task import Capability
from rlgate.ingest.bank import discover_tasks, load_bank
from rlgate.ingest.mapping import BankMapping
from rlgate.ingest.presets import load_preset, preset_names


def test_presets_available():
    assert set(preset_names()) == {"mimo", "swebench", "harbor", "deepswe"}


def test_mimo_preset_dir_layout(tmp_path):
    env = tmp_path / "envs" / "task-x"
    (env / "image_fs" / "testbed").mkdir(parents=True)
    (env / "problem_statement.md").write_text("make it optional\n")
    (env / "env.json").write_text(json.dumps({
        "docker_image": "repo/img:task-x", "language": "typescript",
        "test_command": "bash /testbed/mimo_test_command.sh",
    }))
    tasks = list(load_preset("mimo").resolve_all(tmp_path))
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "task-x"
    assert t.instruction.strip() == "make it optional"
    assert t.repo_dir == (env / "image_fs" / "testbed").resolve()
    assert t.grader.image == "repo/img:task-x"
    assert "bash" in t.grader.run
    assert t.languages == frozenset({"typescript"})
    assert Capability.CONTAINER in t.capabilities and Capability.GOLD_PATCH not in t.capabilities


def test_swebench_preset_record_layout(tmp_path):
    jsonl = tmp_path / "verified.jsonl"
    jsonl.write_text(json.dumps({
        "instance_id": "django__django-1", "problem_statement": "fix the bug",
        "patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
        "image_name": "swebench/django:1",
    }) + "\n")
    tasks = load_bank(jsonl, preset="swebench")
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "django__django-1"
    assert t.gold_patch is not None and Capability.GOLD_PATCH in t.capabilities
    assert t.grader.image == "swebench/django:1"
    assert t.instruction == "fix the bug"


def _harbor_task(root: Path, name: str, with_gold: bool) -> None:
    d = root / "tasks" / name
    (d / "environment").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "instruction.md").write_text("do the thing\n")
    (d / "environment" / "Dockerfile").write_text("FROM python:3.12.4-slim\n")
    (d / "tests" / "test.sh").write_text("pytest -q\n")
    (d / "task.toml").write_text(
        'name = "x"\nlanguage = "go"\n[[verifier.collect]]\ndocker_image = "ecr/x:1"\n')
    if with_gold:
        (d / "solution").mkdir()
        (d / "solution" / "solution.patch").write_text(
            "--- a/m\n+++ b/m\n@@ -1 +1 @@\n-a\n+b\n")


def test_harbor_preset_no_gold(tmp_path):
    _harbor_task(tmp_path, "cad-model", with_gold=False)
    [t] = load_bank(tmp_path, preset="harbor")
    assert t.id == "cad-model"
    assert t.grader.run == "bash tests/test.sh"
    assert t.grader.image.endswith("environment/Dockerfile")  # a path hygiene.dockerfile lints
    assert Capability.GOLD_PATCH not in t.capabilities


def test_deepswe_preset_reads_toml_and_gold(tmp_path):
    _harbor_task(tmp_path, "abs-flags", with_gold=True)
    [t] = load_bank(tmp_path, preset="deepswe")
    assert t.gold_patch is not None and Capability.GOLD_PATCH in t.capabilities
    assert t.grader.image == "ecr/x:1"          # recursive find in task.toml's [[verifier.collect]]
    assert t.languages == frozenset({"go"})


def test_custom_mapping_for_novel_layout(tmp_path):
    # A layout matching no preset: cases/<id>/desc.txt — handled by config, not code.
    case = tmp_path / "cases" / "c1"
    case.mkdir(parents=True)
    (case / "desc.txt").write_text("hello\n")
    mapping = tmp_path / "bank.rlgate.yaml"
    mapping.write_text(
        "name: custom\nenumerate: {dirs: 'cases/*'}\n"
        "map:\n  id: '{dir.name}'\n  instruction: {file: desc.txt}\n"
        "  grader_run: {literal: 'true'}\n")
    [t] = load_bank(tmp_path, mapping=mapping)
    assert t.id == "c1" and t.instruction.strip() == "hello"


def test_discover_and_limit():
    tasks = list(discover_tasks("examples/taskbank"))
    assert {t.id for t in tasks} == {"clean", "weak_grader"}
    assert len(load_bank("examples/taskbank", limit=1)) == 1


def test_from_file_roundtrip(tmp_path):
    p = tmp_path / "m.bank.yaml"
    p.write_text("name: m\nenumerate: {records: '*.jsonl'}\nmap: {id: {field: id}}\n")
    assert BankMapping.from_file(p).name == "m"
