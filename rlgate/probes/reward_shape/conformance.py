"""§3.5 — reward-shape conformance linter (A1–A7). Pure static inspection of the normalized
ScoringModel (no execution, no LLM). Honest scope: this applies only to tasks that declare a
scoring spec a schema can parse; environments whose reward is just test pass/fail have no model
and the grader-strength probes carry the load instead.

Each A-check is a concrete structural fact about the declared reward, so all are CONFIRMED.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task
from rlgate.scoring import FEATURE_SIGNALS, PARTICIPATION_SIGNALS, ScoringModel, load_model

_CORRECT_RH_RULE = "eval_high_train_low"


@register
class RewardShapeConformance(Probe):
    meta = ProbeMeta(
        id="reward_shape.conformance",
        family="reward_shape",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        requires=frozenset({Capability.SCORING_SPEC}),
        doc="Reward-shape design debt in a declared scoring spec (A1–A7).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        model = load_model(task)
        if model is None:
            return []
        findings: list[Finding] = []
        for check in (self._a1, self._a2, self._a3, self._a4, self._a5, self._a6, self._a7):
            findings.extend(check(model))
        return findings

    def _f(self, code: str, title: str, sev: Severity, detail: str, fix: str,
           loc: str | None = None) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            title=f"[{code}] {title}",
            severity=sev,
            verdict=VerdictTier.CONFIRMED,
            location=loc,
            evidence=Evidence(kind="code_line", detail=detail),
            fix_hint=fix,
        )

    def _a1(self, m: ScoringModel) -> list[Finding]:
        dead = [c for c in m.by_signal({"held_out"}) if not c.enters_train]
        return [self._f(
            "A1", f"held-out signal '{c.name}' never enters the training reward",
            Severity.HIGH,
            f"component '{c.name}' (signal held_out) has enters_train=false; held-out coverage "
            "is computed but does not affect r_train, so it gates nothing.",
            "route the held-out signal into r_train (as a gate or weighted term).",
            loc=c.name,
        ) for c in dead]

    def _a2(self, m: ScoringModel) -> list[Finding]:
        if m.milestone_weights and not m.milestones_used:
            return [self._f(
                "A2", "milestone weights are defined but unused",
                Severity.MEDIUM,
                f"milestone weights {m.milestone_weights} are declared but used_in_reward=false "
                "— cosmetic weights that do not affect the reward.",
                "either apply the milestone weights in the reward or delete them.",
            )]
        return []

    def _a3(self, m: ScoringModel) -> list[Finding]:
        keys: dict[str, set[float]] = {}
        where: dict[str, set[str]] = {}
        for fname, weights in m.weights_by_file.items():
            for k, v in weights.items():
                keys.setdefault(k, set()).add(float(v))
                where.setdefault(k, set()).add(fname)
        out = []
        for k, vals in keys.items():
            if len(vals) > 1:
                out.append(self._f(
                    "A3", f"inconsistent weight for '{k}' across scoring files",
                    Severity.HIGH,
                    f"'{k}' is weighted {sorted(vals)} across {sorted(where[k])}; the files "
                    "disagree, so the effective reward depends on which one is authoritative.",
                    "unify the weight in one source of truth; delete the dead config block.",
                    loc=k,
                ))
        return out

    def _a4(self, m: ScoringModel) -> list[Finding]:
        gates = [c for c in m.components if c.gate]
        return [self._f(
            "A4", f"multiplicative gate '{c.name}' can zero the whole reward",
            Severity.HIGH,
            f"component '{c.name}' (signal {c.signal}) is a multiplicative gate; when it is 0 "
            "the entire reward collapses to 0 regardless of real progress (the p2p cliff).",
            "make the gate additive/soft, or ensure it cannot be 0 for legitimate partial work.",
            loc=c.name,
        ) for c in gates]

    def _a5(self, m: ScoringModel) -> list[Finding]:
        out = []
        for c in m.components:
            if c.signal in PARTICIPATION_SIGNALS and set(c.gated_by) & FEATURE_SIGNALS:
                out.append(self._f(
                    "A5", f"participation term '{c.name}' is gated on feature success",
                    Severity.HIGH,
                    f"component '{c.name}' (signal {c.signal}) is gated by {list(c.gated_by)}; "
                    "it only pays out when the feature already passes, so it measures nothing "
                    "independent of f2p.",
                    "ungate the participation term, or drop it if it is redundant with f2p.",
                    loc=c.name,
                ))
        return out

    def _a6(self, m: ScoringModel) -> list[Finding]:
        rule = m.reward_hacking_rule
        if rule and rule != _CORRECT_RH_RULE:
            return [self._f(
                "A6", "reward_hacking_suspected heuristic points the wrong way",
                Severity.HIGH,
                f"rule is {rule!r}; the degenerate hack (e.g. always-exit(0)) drives eval high "
                "and train low, so a correct heuristic is "
                f"{_CORRECT_RH_RULE!r}. This rule flags the wrong direction.",
                f"set the heuristic to {_CORRECT_RH_RULE!r} (eval high + train low ⇒ suspect).",
            )]
        return []

    def _a7(self, m: ScoringModel) -> list[Finding]:
        keys: dict[str, set[object]] = {}
        where: dict[str, set[str]] = {}
        for fname, meta in m.metadata_by_file.items():
            for k, v in meta.items():
                keys.setdefault(k, set()).add(v)
                where.setdefault(k, set()).add(fname)
        out = []
        for k, vals in keys.items():
            if len(vals) > 1:
                out.append(self._f(
                    "A7", f"contradictory metadata '{k}' across scoring files",
                    Severity.MEDIUM,
                    f"'{k}' is {sorted(map(str, vals))} across {sorted(where[k])}; stale or "
                    "contradictory metadata means at least one file is wrong.",
                    "regenerate metadata from one source; fail CI on cross-file drift.",
                    loc=k,
                ))
        return out
