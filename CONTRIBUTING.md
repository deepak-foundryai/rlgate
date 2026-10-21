# Contributing to rlgate

## Add a probe = add two files

1. **The probe** — `rlgate/probes/<family>/<name>.py`: a `@register`-decorated `Probe`
   subclass with a `ProbeMeta`. Discovery picks it up automatically; there is **no central
   list to edit** and no pipeline change.
2. **A fixture** — `tests/fixtures/<family>/<name>/` with a `bad/` task your probe must flag
   and an `oracle/` (clean) task it must stay silent on, plus a test in `tests/probes/`.

That's it. CI runs your fixture; the probe catalog regenerates from `ProbeMeta`.

### Rules

- **Gate on capabilities, never on a benchmark name.** Use `ProbeMeta.requires`; never read
  `task.source` or branch on a format. If you need source-specific data it lives in
  `task.meta`, and only a companion probe for that source may read it.
- **Deterministic generation.** Any new variant generator / mutation operator must follow
  ARCHITECTURE §2a: no RNG, content-addressed IDs, versioned operator set. Scoring
  nondeterminism (flaky tests) is handled by the baseline, not your probe.
- **A finding is evidence, not a score.** Return a reproduced exploit or a concrete
  `file:line` with a `replay` command and a `fix_hint`. Never classify from a reward value.
- **Mark `kind` honestly.** `GENERIC` = runs on any task with the capabilities. `PATTERN` =
  needs per-grader understanding (§3.6–§3.8); say so.
- **Set `needs_live`** if the probe requires a real container run (e.g. race probes).

## Add a new environment type

Prefer the declarative descriptor (`task.rlgate.yaml`) — most environments need **no code**.
Only add a preset under `rlgate/ingest/presets/` for a popular layout, or a scoring-schema
plugin under `rlgate/ingest/schemas/` for a new reward format.

## Dev loop

```bash
uv pip install -e ".[dev]"
ruff check .
pytest -q
```
