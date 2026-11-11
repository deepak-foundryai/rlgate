# Changelog

## Unreleased

### Changed: what an existing command line does

- **Steps 3-6 all run by default**, even after one fails, so every problem shows in one pass.
  A broken build or baseline still stops the run. `-x` / `--fail-fast` restores the old
  stop-at-the-first-failure behaviour; `--keep-going` is accepted as a no-op.
- **Steps 3-6 run at the same time by default** for one task on a terminal (a live view of
  each step). A bank stays one step at a time per task. `parallel = false` under
  `[checks]` in `rlgate.toml` turns it off.

### Renamed (the old names still work, with a notice, until the next release)

| Old | New |
| --- | --- |
| `rlgate show <rule> <task>` | `rlgate explain <rule> <task>` |
| `rlgate rules` | `rlgate explain` (no arguments) |
| `rlgate waivers` | `rlgate waive --list` |
| `rlgate cache clear` | `rlgate clean` (hidden) |
| `rlgate planted build` / `score` | `rlgate selftest DIR` (hidden; builds, checks and scores in one pass) |
| `--deep` | `--deep-review` |
| `--depth full` | `--exhaustive` (`--depth fast` is the default) |
| `--step N` | `--only N` (names or 1-6, comma-separated) |
| `--ek key=value` | `--backend-opt key=value` |
| `--preset` / `--mapping` / `--adapter` | `--layout NAME or FILE` (also `layout` in `rlgate.toml`) |
| `--workers N` | `workers` under `[checks]` in `rlgate.toml` |
| `-j` / `-o` as global options | options of `check` (and `-o` of `report`); the global form still works |

### Added

- `rlgate waive --remove <rule> <task>` deletes a waiver.
- `--from` takes a step name as well as a number.
- `check` prints the exact `rlgate explain <rule> <task>` line under every finding, and lists
  every open finding, not only those of the step that stopped the run.
- A **Not checked** verdict for tasks the backend failed on (Daytona unreachable, an API
  error): it says nothing about the task, and a rerun without `--fresh` redoes only those.
- Daytona account limits (snapshots; CPU, memory and disk per region) are read from the
  account at run time and respected; see the README.
