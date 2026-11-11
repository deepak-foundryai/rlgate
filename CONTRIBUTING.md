# Contributing to rlgate

## Add a probe = add two files

1. **The probe** — `rlgate/probes/<family>/<name>.py`: a `@register`-decorated `Probe`
   subclass with a `ProbeMeta`. Discovery picks it up automatically; there is **no central
   list to edit** and no pipeline change.
2. **A fixture** — `tests/fixtures/<family>/<name>/`: a task your probe must flag, and a
   clean one it must stay silent on, plus a test in `tests/probes/`.

Every finding a probe reports carries a stable rule ID. A new kind of finding also needs:

- an entry in `rlgate/core/rules.py` (ID in its step's range, title, severity, proof kind);
- its What / Why / Fix text and a bad and a good example in `rlgate/core/rule_docs.py`
  (what `rlgate explain <rule>` shows);
- the catalogue regenerated:
  `python -c "from rlgate.core.rule_docs import catalogue_markdown as c; open('docs/rules.md','w').write(c())"`.

### Rules

- **Gate on capabilities, never on a benchmark name.** Use `ProbeMeta.requires`; never read
  `task.source` or branch on a format. If you need source-specific data it lives in
  `task.meta`, and only a companion probe for that source may read it.
- **Deterministic generation.** A new variant generator or mutation operator uses no RNG,
  gives content-addressed IDs, and versions its operator set, so a finding replays exactly.
  Scoring nondeterminism (flaky tests) is handled by the baseline, not your probe.
- **A finding is evidence, not a score.** Return a reproduced exploit or a concrete
  `file:line` with a `replay` command and a `fix_hint`. Never classify from a reward value.
- **Mark `kind` honestly.** `GENERIC` = runs on any task with the capabilities. `PATTERN` =
  needs per-grader understanding; say so.
- **Set `needs_live`** if the probe requires a real container run (e.g. race probes).

## Add a new environment type

Prefer the declarative descriptor (`task.rlgate.yaml`) — most environments need **no code**.
A bank in another layout can be described in a `bank.rlgate.yaml` mapping and checked with
`rlgate check DIR --layout ./bank.rlgate.yaml`; add a preset under `rlgate/ingest/presets/`
only for a popular layout, or a scoring-schema plugin under `rlgate/ingest/schemas/` for a
new reward format.

## Dev loop

```bash
uv pip install -e ".[dev]"
ruff check .
pytest -q
rlgate selftest ./planted     # rlgate against tasks with planted defects
```

Tests that need Docker, Daytona, a model key or a large task bank skip themselves unless
configured; see the skip reasons in `pytest -rs`.
