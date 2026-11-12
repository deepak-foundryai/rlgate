# Changelog

## 0.1.6 (2026-11-12)

### Added

- **SWE-bench-style rows are graded the way SWE-bench grades them.** `rlgate check rows.jsonl`
  (or a folder of them) loads each row natively: the workspace is the row's evaluation image,
  its `eval_script` is held out until grading starts, and a pass is SWE-bench's
  RESOLVED_FULL: the log is read with the row's own `log_parser` (SWE-bench's parsers,
  vendored under their MIT licence, for Python, JavaScript, Go, Rust, Java, PHP, Ruby and C),
  every FAIL_TO_PASS test must pass and no PASS_TO_PASS test fail. A run SWE-bench calls
  invalid (the test patch didn't apply, no output markers, a test command that failed while
  the log shows no failure) is no pass. Works on the current SWE-bench, _Lite, _Verified and
  _Multilingual datasets as published.
- `benchmarks/mbpp-plus` labels no longer expect RG001 (reference fails): MBPP's tests were
  written from its reference, which passes them; the flaws are too-strict tests or unclear
  tasks, which the deep review checks.
- `benchmarks/fetch.py`: ImpossibleBench rows get their Verified twin's eval script with the
  mutated test patch swapped in; SWE-bench full and Lite rows come from the `SWE-bench/`
  datasets, which carry eval scripts. On networks where IPv6 doesn't connect, fetching falls
  back to IPv4 instead of hanging.

### Fixed

- "The agent can read the held-out tests" (RG212) checks the folders the grader actually
  holds out (Harbor's `/tests`, SWE-bench's eval script), and only before grading. It used
  to check a `tests/` folder in the workspace, so a repo's own test suite (Django's, say)
  was reported as a leak.
- SWE-bench rows no longer get "doesn't declare its resource limits" (RG012): the format has
  nowhere to declare them. Their reports' commands name the row file, not an internal
  folder.
- Held-out tests of a `task.rlgate.yaml` task (`tests: [{from: …, to: …}]`) now reach the
  grader on Daytona and Docker. They were only copied on the local backend, so there every
  grade failed with "file or directory not found" and the reference was reported as failing
  (RG001). Affects the single-function benchmarks (MBPP, HumanEval, BigCodeBench) and any
  YAML task with held-out tests.
- A compose service that never accepts commands gets its stack restarted once; if it still
  doesn't, the error says what the last attempt printed (it said only "never became ready").
- `rlgate check .` names its run folder after the folder (`foundryai-coding-data_…`), not
  `run_…`.
- Sandboxes held through a long run are no longer deleted under it. Daytona stopped (and so
  deleted) a sandbox after 30 minutes without API calls, so a compose stack host that sat
  idle while other tasks ran failed later with `DaytonaNotFoundError: … not found`. Every
  held sandbox is now kept alive, and a stack host idle for 10 minutes is checked and
  replaced if it's gone.
- An LLM call can no longer hang a task for hours. Each answer has one deadline (15 minutes
  by default, `--llm-timeout MINUTES` to change it) that covers its retries, and a call that
  runs out says the endpoint gave no answer in time and suggests a lower `-j` or a faster
  model.
- Ctrl-C stops a run at once: queued tasks are cancelled, the sandboxes it holds are
  deleted, and it exits with "Interrupted." (Typer had turned Ctrl-C into a normal exit, so
  the run then waited for its busy workers and seemed to hang.) `kill` does the same.
- A run that is stopped part-way keeps what it finished: each task's `tasks/<id>/` report is
  written as soon as that task is done, not only when the whole bank is.
- Compose stacks on Daytona no longer fail with Docker Hub's "429 Too Many Requests": the
  Docker daemon in the sandbox pulls Docker Hub images through Google's public mirror
  (`mirror.gcr.io`) and falls back to Docker Hub. `--backend-opt registry_mirror=` turns it
  off; `registry_mirror=<url>` uses your own.

### Known limitations

- The agentic audits can use their whole turn budget on a large task without grading a
  candidate; the check then says "no verdict" (it never guesses). Give them more room with
  a stronger `--agent-model`.
- RG009 assumes training grades the agent's work as a patch of the workspace; a Harbor task
  whose reference installs software outside it is reported even if your harness grades in
  place.

## 0.1.5 (2026-11-12)

0.1.4 was tagged but never reached PyPI (its release build never started); 0.1.5 carries
everything in it.

### Changed

- **Run folders are named target first:** `tb2_2026-11-12_10-09/` (target or `--job-name`,
  then date and time to the minute; a second run in the same minute gets `-2`), instead of
  `2026-11-12__10-09-25__tb2/`.

### Fixed

- **Harbor tasks that collect the agent's work are graded correctly.** A task's
  `[[verifier.collect]]` commands (for example `git diff <base> HEAD >
  /logs/artifacts/model.patch`, used by DeepSWE) now run where the agent worked, after it
  finishes and before its artifacts cross to a separate verifier. Before, the verifier never
  got the patch, graded the untouched repository, and every such task looked broken
  (RG001). A submitted patch is committed first, as an agent submitting it would;
  uncommitted work is still not graded, as in the harness.
- The QA card counted do-nothing runs against the reference's run count ("do-nothing 1/2
  passes" when one do-nothing run had failed); it now counts the do-nothing runs.
- In a run of several tasks, every line of `rlgate.log` names its task (`[tb2-regex-log]`),
  so a failed grading can be traced to the task it belongs to.
- `benchmarks/deepswe`: the tasks at the pinned commit are Harbor tasks; run them without
  `--layout deepswe` (which read them wrongly and failed to apply the reference patch).

## 0.1.4 (2026-11-12)

### Fixed

- **A command that asks a question no longer waits forever.** Every command rlgate runs
  (on Daytona, in a compose stack, or on this machine) now gets no input, as `docker exec`
  gives the harness: a prompt such as apt's tzdata "Geographic area:" sees end-of-input and
  takes its default. On Daytona a long command ran in a session that kept its input open,
  so a reference solution that installs packages hung until its time limit on every run
  (Terminal-Bench 2 `adaptive-rejection-sampler`).
- `benchmarks/`: 28 task paths corrected (the 19 Terminal-Bench 2.1 controls live under
  `tasks/`, 9 Terminal-Bench 1 tasks under `original-tasks/`). `fetch.py` no longer leaves an
  empty folder for an entry it couldn't fetch; it lists every failure at the end and exits 1.

## 0.1.3 (2026-11-11)

### Fixed

- **Compose tasks on Daytona no longer hang or crawl.** 0.1.1 ran a task's stacks one at a
  time on one sandbox; with steps 3-6 and the agents running together they queued behind
  each other (the hack agent holds a stack for its whole session) and could wait forever.
  Now each live stack gets its own Docker-in-Docker sandbox, sized for one stack as the
  harness runs it: sandboxes are added as needed, up to `stack_hosts` per task (default 4,
  `--backend-opt stack_hosts=N`), each building the task's images once. A thread that
  already holds a stack never waits for a second one.
- **`--model` now drives the agents too.** An `agent` model in an `rlgate.toml` no longer
  overrides the model you pass on the command line; `--agent-model` picks the agents' model
  separately. The run prints which model each role uses.
- **A model that can't answer stops the run before it starts:** `--deep-review` makes one
  tiny call per model first, so a wrong model name shows at once instead of every judge
  failing later.
- `pip install 'rlgate[llm]'` installs `tenacity`, which LiteLLM needs to retry a call;
  without it a failing call showed "tenacity import failed" instead of the real error.
- **A stack that fails to start is retried once** from scratch (a database's first-boot
  race, a slow disk) before the task counts as not checked; if it fails again the error
  names the stopped containers and their exit codes.
- **A judge answer cut off at `max_tokens`** (a reasoning model can spend its budget
  thinking) is asked once more with twice the room, same model, up to 65,536 tokens.
- A reference solution that runs git no longer makes git's own files (`.git/ORIG_HEAD`,
  logs) count as untested parts of the fix (RG101).
- Each agent turn is logged in `rlgate.log` (the model's time and the tools it called);
  a check that doesn't apply is logged as such, not as failed.
- The cost line names how to price a model LiteLLM doesn't know (the hint was cut out of
  the message).
- The single-task progress line is kept well inside the terminal width, so it can't wrap
  and repeat down the screen.

## 0.1.2 (2026-11-11)

### Fixed

- **A reference solution that changes a file that isn't UTF-8** (Latin-1 source, a stray
  byte) no longer crashes the check ("UnicodeDecodeError"): such files are diffed as binary,
  so the patch still applies byte for byte.
- **Commands that print bytes that aren't UTF-8** (a grader, a build, git) no longer crash
  the check on Docker, local, compose or Daytona-compose runs.
- **The terminal stays readable during a run:** a warning or error shows as one line,
  ending "(details in rlgate.log)"; tracebacks and command output go only to `rlgate.log`. A
  failing reference solution's output is no longer printed (it is reported as RG001).

### Added

- `benchmarks/score.py`: scores rlgate runs against the labelled benchmarks and, with
  `--publish`, writes the score and each task's report into `benchmarks/results/` with
  local paths removed.

## 0.1.1 (2026-11-11)

### Fixed

- **Compose tasks on Daytona** (a task with its own `docker-compose.yaml`, such as main +
  a service + a database): the gradings of one task ran their stacks at the same time on
  one sandbox sized for a single stack, so containers ran out of memory and grading failed
  with "Could not find the file /tmp in container". On Daytona these stacks now run one at
  a time, as the harness runs them. Local Docker is unchanged.
- When a container of a stack stops, the error names it with its exit code and whether it
  ran out of memory, and suggests `--backend-opt memory_gb=N` when it did.
- **`rlgate.log` records the run:** each step's start, end and duration, each grading with
  its exit code and time (the grader's output when it fails), and the full traceback of a
  task that could not be checked.
- A model behind an OpenAI-compatible endpoint (`openai/deepseek-ai/DeepSeek-V4.1-Flash` on
  Tinker, for example) is counted as its own family, not OpenAI.
- A task that could not be checked shows how long it ran instead of `0.0s`.

### Added

- `benchmarks/`: published independent audits of public benchmarks and RL environments,
  one folder per benchmark with the reports behind every labelled task, and `fetch.py`
  to download them for `rlgate check`.
- `--only questions` is listed in `rlgate check --help`: the three LLM questions on their own
  (with `--deep-review`).

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
