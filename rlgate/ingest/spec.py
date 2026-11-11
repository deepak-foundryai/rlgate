"""Generic, declarative ingestion: describe ANY environment in `task.rlgate.yaml` and get a
`Task`. Capabilities are derived from which keys are present — no benchmark-specific code.

Example:
    instruction_path: TASK.md
    workspace: ./repo
    gold_patch: ./solution/fix.patch        # optional → GOLD_PATCH probes
    grader:
      run: "bash run_tests.sh"
      image: ./Dockerfile                   # optional → CONTAINER
      report: {format: junit, path: /out/results.xml}   # optional → PER_TEST
      select: "pytest {tests} --junitxml=/out/results.xml"  # optional → subset runs
      verdict: exit_code                    # optional: exit_code (default) | report | both |
                                            #   reward_file (with reward_files: [path, …])
      tests: [{from: ./hidden, to: .graded}]  # optional: held-out tests, copied in at grading
    scoring_spec:                           # optional → reward_shape probes (A1–A7)
      schema: declarative                   # a registered rlgate.scoring schema plugin
      path: ./scoring.rlgate.yaml
    languages: [python]
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rlgate.core.task import GraderSpec, Patch, ReportSpec, ScoringSpec, Task

DESCRIPTOR_NAME = "task.rlgate.yaml"


def load(descriptor_path: str | Path) -> Task:
    path = Path(descriptor_path)
    if path.is_dir():
        if not (path / DESCRIPTOR_NAME).is_file():
            from rlgate.ingest import harbor
            if harbor.is_task_dir(path):
                return harbor.load(path)
        path = path / DESCRIPTOR_NAME
    elif path.name == "task.toml":
        from rlgate.ingest import harbor
        return harbor.load(path.parent)
    data = yaml.safe_load(path.read_text())
    base = path.parent

    def rel(p: str) -> Path:
        return (base / p).resolve()

    g = data["grader"]
    report = None
    if g.get("report"):
        report = ReportSpec(format=g["report"]["format"], path=g["report"]["path"])
    grader = GraderSpec(
        run=g["run"],
        image=str(rel(g["image"])) if g.get("image") else None,
        report=report,
        pass_reward=float(g.get("pass_reward", 1.0)),
        select=g.get("select"),
        verdict=g.get("verdict", "exit_code"),
        # held-out tests: {from: host dir, to: where they appear} copied in only at grading
        inject=tuple((str(rel(t["from"])), t["to"]) for t in g.get("tests") or ()),
        reward_files=tuple(g.get("reward_files") or ()),
        reward_key=g.get("reward_key", "reward"),
    )

    instruction = None
    if data.get("instruction_path"):
        instruction = rel(data["instruction_path"]).read_text()
    elif data.get("instruction"):
        instruction = data["instruction"]

    gold = None
    if data.get("gold_patch"):
        gold = Patch(text=rel(data["gold_patch"]).read_text())

    scoring = None
    if data.get("scoring_spec"):
        s = data["scoring_spec"]
        scoring = ScoringSpec(schema=s["schema"], path=str(rel(s["path"])))

    return Task(
        id=data.get("id", path.parent.name),
        source=data.get("source", str(path)),
        repo_dir=rel(data["workspace"]),
        grader=grader,
        instruction=instruction,
        gold_patch=gold,
        scoring_spec=scoring,
        languages=frozenset(data.get("languages", [])),
        meta=data.get("meta", {}),
        origin=str(path.parent),
    )
