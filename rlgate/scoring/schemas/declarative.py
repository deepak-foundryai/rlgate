"""The native rlgate scoring schema: an author declares the reward structure directly in a
YAML file and gets A1–A7 linting immediately. Per-benchmark schemas (e.g. a Harbor ``score.py``
AST adapter) are separate plugins that populate the same ``ScoringModel``.

Example (``scoring.rlgate.yaml``)::

    components:
      - {name: f2p,     signal: f2p,      weight: 0.7, enters_train: true}
      - {name: p2p,     signal: p2p,      weight: 1.0, gate: true, enters_train: true}
      - {name: held,    signal: held_out, weight: 0.0, enters_train: false}
      - {name: lintb,   signal: lint_build, weight: 0.1, gated_by: [f2p]}
    milestones: {weights: {m1: 0.2, m2: 0.3}, used_in_reward: false}
    weights_by_file:
      task.json:           {f2p: 0.7, p2p: 0.3}
      scoring_config.json: {f2p: 0.6, p2p: 0.4}
    reward_hacking_rule: eval_high_train_low
    metadata_by_file:
      task.json:           {num_tests: 44}
      scoring_config.json: {num_tests: 43}
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rlgate.scoring.base import ScoringSchema, register_schema
from rlgate.scoring.model import RewardComponent, ScoringModel


@register_schema
class DeclarativeSchema(ScoringSchema):
    name = "declarative"

    def parse(self, path: Path) -> ScoringModel:
        data = yaml.safe_load(Path(path).read_text()) or {}
        components = [
            RewardComponent(
                name=c["name"],
                signal=c.get("signal", c["name"]),
                weight=c.get("weight"),
                enters_train=bool(c.get("enters_train", True)),
                gate=bool(c.get("gate", False)),
                gated_by=tuple(c.get("gated_by", ())),
            )
            for c in data.get("components", [])
        ]
        milestones = data.get("milestones", {}) or {}
        return ScoringModel(
            schema=self.name,
            components=components,
            milestone_weights=dict(milestones.get("weights", {})),
            milestones_used=bool(milestones.get("used_in_reward", True)),
            weights_by_file={k: dict(v) for k, v in data.get("weights_by_file", {}).items()},
            metadata_by_file={k: dict(v) for k, v in data.get("metadata_by_file", {}).items()},
            reward_hacking_rule=data.get("reward_hacking_rule"),
            source_files=[Path(path).name],
        )
