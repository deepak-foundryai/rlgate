# rlgate

**A deterministic, third-party auditor that finds reward-hacking vulnerabilities in RL/eval
environments** — before you train on them.

rlgate is not an environment framework and not a trajectory benchmark. It *consumes* any
environment (via a one-file declarative descriptor) and attacks its grader with deterministic
probes: it reverts hunks of the gold fix to find unguarded tests, plants forged artifacts to
find grading-channel forgery, walks the git object store to find leaked answers, and lints the
reward formula for design debt. Every finding is a **reproduced exploit or a line of code** —
never a threshold on a score.

See **[PLAN.md](PLAN.md)** for the methodology and **[ARCHITECTURE.md](ARCHITECTURE.md)** for
the design.

## Quickstart

Run it as an isolated tool straight from the repo (no clone, no install):

```bash
uvx --from git+https://github.com/deepak-foundryai/rlgate rlgate list-probes
uvx --from git+https://github.com/deepak-foundryai/rlgate rlgate audit path/to/env --gate
```

Or for development:

```bash
uv pip install -e ".[dev]"      # or: pip install -e ".[dev]"
rlgate list-probes
rlgate audit path/to/env --gate # exits non-zero on FIX/DROP (CI mode)
```

Once published to PyPI: `uvx rlgate audit ./env` or `uv tool install rlgate`.

## Task banks

Audit many tasks at once. Either a tree of `task.rlgate.yaml` descriptors, a bundled
benchmark preset, or a declarative mapping for any other layout — the core and all probes are
untouched in every case:

```bash
rlgate audit-bank ./examples/taskbank                 # discover task.rlgate.yaml (live)
rlgate audit-bank ./swebench_verified.jsonl --preset swebench
rlgate audit-bank /path/to/terminal-bench --preset harbor
rlgate audit-bank /path/to/deep-swe --preset deepswe
rlgate audit-bank ./data/mimo_code_envs --preset mimo --static   # offline, no container
rlgate audit-bank ./my_bench --mapping ./bank.rlgate.yaml        # custom layout, no code
```

Presets (`mimo`, `swebench`, `harbor`, `deepswe`) are declarative mapping files under
`rlgate/ingest/presets/`; a new benchmark is a copy-and-edit, not a Python module. `--static`
runs only the probes that read the task on disk (leakage / forgery / hygiene / reward-shape),
so a whole bank can be swept offline before any container is built.

Describe any environment once — no code:

```yaml
# task.rlgate.yaml
workspace: ./repo
gold_patch: ./solution/fix.patch        # optional → enables the revert probe
grader:
  run: "bash run_tests.sh"
  report: {format: junit, path: /out/results.xml}
```

Capabilities are derived from which keys you fill in; probes that need a missing capability
skip silently. Nothing in a probe mentions a benchmark.

## Status

Milestone 0: core contracts + local sandbox + baseline invariant + generic ingestion + three
probes (leakage, revert, forgery) + fixtures + CI. Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed.
