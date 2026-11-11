# Changelog

## 0.1.0 (2026-11-11)

### Changed: what an existing command line does

- **Steps 3-6 all run by default**, even after one fails, so every problem shows in one pass.
  A broken build or baseline still stops the run. `-x` / `--fail-fast` restores the old
  stop-at-the-first-failure behaviour; `--keep-going` is accepted as a no-op.
- **Steps 3-6 run at the same time by default** for one task on a terminal (a live view of
  each step). A bank stays one step at a time per task. `parallel = false` under
  `[checks]` in `rlgate.toml` turns it off.

### Added

- **A folder per run** (`./rlgate-jobs/<date>__<time>__<target>/`): `config.json`,
  `result.json`, `report.md`, `report.html`, `rlgate.log`, and `tasks/<id>/` per task.
  `--jobs-dir`, `--job-name`, or `jobs_dir` under `[checks]`.
- **Faster full runs, same findings:**
  - the baseline stops once two agreeing runs prove a task broken (3 gradings, not 10);
  - checks that only inspect the environment (step 3) no longer need the baseline, so
    `--only leaks` / `--only RG301` skip the reference and do-nothing gradings;
  - one grading is one command (the reward and report files come back with it);
  - Daytona: long commands run in the background and are polled, so a dropped
    connection no longer loses a task; one shared API client; a cap on live sandboxes
    across all tasks (`max_sandboxes`, default 32); sandboxes are deleted in the background.
- **A failing reference solution is graded** like the harness does, giving RG001 (Broken)
  instead of "Not checked".
- **RG301 finds the change itself in git:** with the reference fix or the hidden test patch
  applied, a changed file that is byte-identical to an object already in git is proven.

- **RG301 also reports refs outside the starting commit's history** (an upstream branch or
  tag behind a fresh "task base" commit, a side branch) and leftover file contents or trees
  in the object store. These need your judgment: the fix may be there, but rlgate can't
  prove it without a reference solution. Refs past the starting commit are still proven.
  Leftover objects are counted in full, however many there are.

- **`--deep-review` no longer estimates the cost or asks before running.** It prints the
  calls, tokens and total cost at the end. `budget_usd` under `[models]` is an optional cap, off by default.
  `--yes` is accepted as a no-op.

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
