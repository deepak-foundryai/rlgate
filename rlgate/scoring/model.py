"""Normalized scoring model — the format-agnostic view the reward-shape linter inspects.

A schema plugin maps a benchmark's actual scoring files (Harbor ``score.py`` + ``task.json``,
a declarative ``scoring.yaml``, …) into this one structure, so the A1–A7 checks never read a
benchmark-specific format. Fields a plugin can't populate stay empty; a check that needs a
field it doesn't have simply doesn't fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Signal kinds the checks reason about. Free-form strings are allowed; these are the ones the
# A-checks recognize. "feature" = the primary pass-to-pass/fail-to-pass success signal.
FEATURE_SIGNALS = {"f2p", "fail_to_pass", "feature", "p2p_feature"}
PARTICIPATION_SIGNALS = {"build", "lint", "lint_build", "participation", "format"}


@dataclass(frozen=True)
class RewardComponent:
    name: str
    signal: str                       # what it measures (f2p / p2p / held_out / build / …)
    weight: float | None = None
    enters_train: bool = True         # does this component flow into the training reward?
    gate: bool = False                # multiplicative gate (can zero the whole reward)?
    gated_by: tuple[str, ...] = ()    # signals this component is conditioned on


@dataclass
class ScoringModel:
    schema: str
    components: list[RewardComponent] = field(default_factory=list)
    milestone_weights: dict[str, float] = field(default_factory=dict)
    milestones_used: bool = True                      # do milestone weights affect the reward?
    weights_by_file: dict[str, dict[str, float]] = field(default_factory=dict)  # A3
    metadata_by_file: dict[str, dict[str, object]] = field(default_factory=dict)  # A7
    reward_hacking_rule: str | None = None            # A6 ("eval_high_train_low" = correct)
    source_files: list[str] = field(default_factory=list)

    def by_signal(self, signals: set[str]) -> list[RewardComponent]:
        return [c for c in self.components if c.signal in signals]
