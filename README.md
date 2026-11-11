# rlgate

```text
$ rlgate demo
rlgate check ./rate-limiter                             quick run · 0.0s

 Step 1  Build     ✓ partly checked  2 checks clean; the rest needs rlgate demo --full (4
                                     warnings)
 Step 2  Baseline  ? to review       1 item needs your judgment
 Step 3  Leaks     ✗ failed          patch leftover: app/limiter.py.orig
 Step 4  Grader    - not run         fix step 3 first
 Step 5  Cheating  - not run         fix step 3 first
 Step 6  Fairness  - not run         fix step 3 first

 STOPPED AT STEP 3 of 6 · NEEDS FIXES   1 proven problem · 2 to review · 5 warnings

 RG303  Patch leftover files contain the fix   PROVEN · high
   What:  app/limiter.py.orig — holds pre/post-patch content verbatim (the fix or its target)
   Fix:   Remove *.orig / *.rej before shipping the task.
   More:  rlgate explain RG303 ./rate-limiter

 After fixing, run: rlgate check ./rate-limiter   (resumes at step 3)

$ rlgate explain RG303 ./rate-limiter
╭────────────────────────────────────────────────────────────────────────────────────╮
│ RG303  Patch leftover files contain the fix                          PROVEN · high │
│   What:   app/limiter.py.orig — holds pre/post-patch content verbatim (the fix or  │
│           its target)                                                              │
│   Why:    .orig / .rej files hold pre- or post-patch content verbatim.             │
│   Proof:  app/limiter.py.orig                                                      │
│   Fix:    Remove *.orig / *.rej before shipping the task.                          │
╰────────────────────────────────────────────────────────────────────────────────────╯
  rlgate explain RG303   what the rule means, with examples
```

**rlgate checks RL training environments for reward-hacking holes, before you train on
them.** Point it at a Harbor task and it tells you three things: whether the task is OK,
what is wrong, and how to fix it. Every finding is either **Proven** (shown by running it,
with a command to reproduce it) or **Needs your judgment** (a real signal a person decides
on). The checks are deterministic: no model is needed.

## Install and run

```bash
uv tool install 'git+https://github.com/deepak-foundryai/rlgate'   # once on PyPI: uvx rlgate check ./task
rlgate demo                    # a bundled, deliberately broken task: real findings in seconds
rlgate check ./my-task         # one Harbor task (instruction.md, environment/, tests/, solution/)
rlgate check ./tasks           # every task in a folder: the bank view
rlgate doctor                  # can this machine run full checks? what's missing?
```

## The six steps

`rlgate check <task>` checks a task in six numbered steps, always in this order, and a step
runs only when the one before it passed: if the gold solution doesn't pass, no grader probe
can be trusted. It stops at the first step with a proven problem and says what to fix.
Warnings and needs-your-judgment items are listed but never stop the run.

| Step | Name | Question it answers | What it checks | Rule IDs |
| --- | --- | --- | --- | --- |
| 1 | Build | Can the task be built and run? | Harbor layout, every Dockerfile (pinning, layer order, broad copies), the grader doesn't download at grading time, every image builds | RG6xx |
| 2 | Baseline | Does the task work? | Gold passes 5 of 5 and doing nothing fails 5 of 5 (each run from scratch, under the task's CPU/memory/network limits); fail-to-pass and pass-to-pass tests; the gold's changes, extracted as a patch, still pass; nothing outside the submission reaches the grader; runs fit their time limits; limits are declared | RG0xx |
| 3 | Leaks | Is the answer hidden from the agent? | The solution or tests on disk in the agent's image; git objects and later commits; caches and patch leftovers; answer-named and late-added files; open internet | RG3xx |
| 4 | Grader | Does the grader really check the fix? | Remove each part of the fix, stub each changed function, mutate the fix, hardcode the visible tests' answers; reward wiring | RG1xx, RG5xx |
| 5 | Cheating | Can a pass be faked? | Plants an agent could make with no fix applied: early exit 0, a fake report, always-equal objects, conftest.py, sitecustomize and .pth hooks, a fake clock, test-selection changes, emptied tests; plus the grading command, report path, test setup files, readable tests, test data the agent can edit | RG2xx |
| 6 | Fairness | Are the task and tests clear and fair? | Wording overfit, internal symbols, brittle assertions | RG4xx |

A **full** run (the default when Docker or Daytona is available) builds every image in step 1
and grades the task in step 2. `--quick` runs the file-only part of each step (seconds, no
Docker) and marks those steps "partly checked". Steps 3-6 all run even when one fails, so
you see every problem at once; a broken build or baseline stops the run (nothing after it
could be trusted), and `-x` stops at the first failing step. After a fix, `rlgate check`
resumes where it makes sense: passed steps whose files didn't change come from the last run.

```bash
rlgate check ./my-task                 # all six steps
rlgate check ./my-task --only grader   # one step (or several: --only leaks,grader; or 1-6)
rlgate check ./my-task --from cheating # start at a step; earlier ones from the last run
rlgate check ./my-task -x              # stop at the first failing step
rlgate check ./tasks -j 20             # every task in a folder, 20 at a time
```

## Commands

| Command | What it does |
| --- | --- |
| `rlgate check <task\|folder>` | Check one task or every task in a folder and give a verdict |
| `rlgate explain` | Every rule ID with its title ([catalogue](docs/rules.md)) |
| `rlgate explain <rule>` | What a rule means, with a bad and a good example |
| `rlgate explain <rule> <task>` | That finding in full: What, Why, Proof, Fix |
| `rlgate repro <rule> <task>` | Re-run the proof of a proven finding (`--show-patch` prints the patch) |
| `rlgate waive <rule> <task> -r "reason"` | Accept a finding and record why; `--list` shows current waivers, `--remove <rule> <task>` deletes one |
| `rlgate report` | Re-render the last result in another format, without re-running |
| `rlgate doctor [target]` | Can this machine run checks; `--ping` tries every key, `--options` lists backend settings |
| `rlgate init` | A starter `rlgate.toml` (`--user` for your own settings) |
| `rlgate demo` | Check the bundled broken task (`--full` also grades it) |

Two more are kept out of the main help: `rlgate clean` deletes saved results (`check
--fresh` covers the everyday need), and `rlgate selftest DIR` tests rlgate itself (below).

### `rlgate check` options

| Group | Option | What it does |
| --- | --- | --- |
| Scope | `--only NAMES` | Only these steps, comma-separated: build, baseline, leaks, grader, cheating, fairness (1-6 work too); also a rule ID |
| | `--from NAME` | Start at that step; earlier steps come from the last run |
| | `--failed` | Re-run only the checks that failed last time |
| | `--fresh` | Ignore saved results and check everything again |
| | `--limit N` | At most N tasks of a bank |
| Depth | `--quick` | File-only checks: seconds, no Docker |
| | `--deep-review` | Add the LLM review: judges read the task, and an agent tries to cheat. Shows the cost and asks first |
| | `--model M` | With `--deep-review`: the judge model; repeat it for a panel |
| | `-y`, `--yes` | With `--deep-review`: don't ask before spending (the budget in `rlgate.toml` still applies) |
| | `--exhaustive` | Grade every mutation instead of skipping variants that can't add a finding |
| | `--runs N` | Times to grade the reference and the do-nothing submission (default 5) |
| Execution | `-j N`, `--jobs N` | Check N tasks at once |
| | `--backend` | Where full runs happen: auto, docker, daytona, local |
| | `--backend-opt key=value` | One backend setting, such as `cpus=2` |
| | `-x`, `--fail-fast` | Stop at the first failing step |
| Output | `-f`, `--format` | terminal, json, markdown, sarif, html, badge |
| | `-o FILE`, `--output FILE` | Write the result to a file (format from the extension) |
| Config | `--config PATH` | Path to an `rlgate.toml` |
| | `--layout NAME or FILE` | Bank layout when it isn't detected: a preset, a mapping file, or an adapter |

Global options, before or after the command: `-v`/`--verbose`, `-q`/`--quiet`,
`--no-color`, `--ascii`, `--version`, `--install-completion`.

In `rlgate.toml`, `[checks]` also takes `workers` (grading runs at once inside a task,
default 4), `parallel` (steps 3-6 at the same time; on by default for one task on a
terminal) and `layout`.

**Exit codes:** 0 ready, 1 needs fixes, 2 broken, not checked, or could not run. **In CI:** a
GitHub Action and a pre-commit hook, see [docs/github-action.md](docs/github-action.md).

**Renamed in this release** (the old names still work, with a notice, until the next one):
`show` → `explain <rule> <task>`, `rules` → `explain`, `waivers` → `waive --list`,
`cache clear` → `clean`, `planted build/score` → `selftest`, `--deep` → `--deep-review`,
`--depth full` → `--exhaustive`, `--step N` → `--only N`, `--ek` → `--backend-opt`,
`--preset/--mapping/--adapter` → `--layout`, `--workers` → `workers` in `rlgate.toml`.
`--keep-going` is now the default (`-x` is the opposite), and `--parallel` is on by default
for one task on a terminal.

## The fix loop

```bash
rlgate doctor --ping
rlgate check ./bank -j 20
rlgate explain RG305 ./bank/task-x                # what, why, proof, fix
rlgate repro RG305 ./bank/task-x                  # watch the problem happen
# …edit the task, or accept the finding:
rlgate waive RG305 ./bank/task-x -r "reason"
rlgate check ./bank -j 20                         # unchanged steps come from the last run
rlgate report -f html -o report.html
```

Waivers live in `rlgate.toml` next to the task, show on every report as "accepted, with
reason", and are re-raised automatically when the files they refer to change. Proven
findings of high severity can't be waived, only fixed.

## Other task formats

Harbor tasks are detected automatically. Other layouts work through a small adapter layer,
without touching the checks:

```bash
rlgate check ./swebench_verified.jsonl             # SWE-bench rows (auto-detected)
rlgate check /path/to/deep-swe --layout deepswe
rlgate check ./data/mimo_code_envs --limit 20      # MiMo (auto-detected)
rlgate check ./my_bench --layout ./bank.rlgate.yaml    # any layout, described in YAML
```

Presets (`harbor`, `swebench`, `deepswe`, `mimo`) are declarative mapping files under
`rlgate/ingest/presets/`; a new benchmark is a copy-and-edit, not a Python module.

### Harbor-format tasks (Terminal-Bench 2 and anything packaged like it)

A directory with `task.toml` + `instruction.md` + `environment/` + `tests/` + `solution/` is
read natively — no Harbor install, nothing delegated to it. `rlgate check <task-dir>` and
`rlgate check <dir>` detect the layout. rlgate grades the task the way the harness
would:

- the workspace is the agent image's workdir (task.toml `workdir`, the compose `main`
  service's `working_dir`, or the Dockerfile's `WORKDIR`); rlgate pulls it out once and
  probes the real files;
- `[verifier] environment_mode = "separate"`: every candidate gets a **fresh** grader
  environment (`[verifier.environment] docker_image`, else `tests/Dockerfile` or
  `tests/docker-compose.yaml`, else a copy of the agent environment), and only the declared
  `artifacts` cross over — so an edit outside them provably never reaches the grader;
- shared mode: `tests/` is uploaded to `/tests` only at grade time, after the candidate ran;
- the reward is what `/tests/test.sh` writes to `/logs/verifier/reward.json` (all entries kept
  as metrics; THE reward is `reward`, else `reward.txt`);
- the oracle is `solution/solve.sh`, run in the agent environment with its effects carried to
  the grader; its workdir changes become the gold patch the revert/mutation probes use (a
  patch file `solve.sh` applies is used directly).

Compose stacks (agent and/or grader) run in one Docker-in-Docker Daytona sandbox per task —
images build once, every candidate gets its own project (`down -v` after) — or on a local
Docker + Compose host. Shapes that can't be run faithfully (multi-step tasks, Windows,
artifacts collected from a sidecar service) still get the quick checks, and `rlgate
doctor <folder>` says why a full run can't happen.

Describe any environment once — no code:

```yaml
# task.rlgate.yaml
workspace: ./repo
gold_patch: ./solution/fix.patch        # optional → enables the grader probes (step 4)
grader:
  run: "bash run_tests.sh"
  report: {format: junit, path: /out/results.xml}
  tests: [{from: ./hidden, to: .graded}]  # optional: held-out tests, copied in only to grade
  verdict: exit_code                    # or report | both | reward_file (+ reward_files)
```

The cheating checks that write outside the workspace (site-packages hooks, a fake report at an
absolute path) need `--backend docker` or `daytona`; on the host backend they show as
"doesn't apply" with that reason, never as passed.

Capabilities are derived from which keys you fill in; probes that need a missing capability
skip silently. Nothing in a probe mentions a benchmark.

## Where tasks run, and which models judge them

`rlgate doctor` tells you what this machine can use and how to fix what's missing
(`--ping` also makes one tiny real call to each service).

**Environments** — pick with `--backend` (or `[environment] type` in `rlgate.toml`):

| Backend | Setup | Notes |
| --- | --- | --- |
| `local` | nothing | grader runs on this machine; no isolation (fixtures, dev) |
| `docker` | a running Docker daemon | builds the task's Dockerfile; one container per grade |
| `daytona` | `pip install 'rlgate[daytona]'` + `DAYTONA_API_KEY` in env or `.env` | builds the task's Dockerfile once into a cached snapshot, one sandbox per task |

`auto` (default) picks Daytona for container tasks when configured, else Docker, else local.
Daytona options go in `rlgate.toml` or on the command line:

```bash
rlgate check ./task --backend daytona --backend-opt cpus=2 --backend-opt memory_gb=4
```

Every Daytona sandbox is labelled `rlgate`, deleted when the check ends, and also has an
auto-stop interval and a hard TTL, so a crashed run can't leave machines billing.

**Account limits.** Every Daytona organization has its own caps (snapshots; CPU, memory and
disk per region), set by its plan. rlgate reads them from the Daytona API at run time
(`rlgate doctor --ping` prints them) and works within them, so a bank of hundreds of tasks
doesn't fail on them:

- Before building a snapshot it takes a free slot. When there is none, it deletes the least
  recently used snapshot **it made itself** (`rlgate-*`) that no task of this run needs and
  nothing used in the last `snapshot_idle_minutes` (another run on the same account may be
  using it). Your own snapshots are never touched. When nothing can be freed it waits for a
  task of this run to finish, then falls back to building the image per sandbox.
- Before creating a sandbox it waits until the region has the CPU, memory and disk for it.
- If the API key can't read the organization's usage, nothing is gated up front; set
  `max_snapshots` to your plan's limit, or rlgate frees one of its own snapshots when Daytona
  refuses a build and retries once.

| Option | Default | |
| --- | --- | --- |
| `max_snapshots` | read from the account | snapshot limit to use when the key can't read it |
| `snapshot_idle_minutes` | 10 | an `rlgate-*` snapshot unused this long may be deleted to make room |
| `snapshot_wait_s` | 1800 | how long to wait for a slot before building per sandbox |
| `room_wait_s` | 600 | how long to wait for CPU/memory/disk before trying anyway |
| `region` | the region with the most CPU | which region's quota to watch |

A task the backend failed on (Daytona unreachable, an API error) is reported as **Not
checked**, not Broken: nothing was learned about it. Rerun the same command without
`--fresh` and only those tasks run again.

**Models** (only for the LLM review, `--deep-review`; the default checks use none) — any LiteLLM
`provider/model` name, with the provider's own key variable, as in Harbor. Pick the judges
per run with `--model` (repeat it for a panel; the votes take turns through it), or in
`rlgate.toml`:

```bash
pip install 'rlgate[llm]'
export OPENAI_API_KEY=... GEMINI_API_KEY=...   # whichever providers you use
rlgate doctor --ping                           # checks every judge with one tiny call
rlgate check ./task --deep-review --model openai/<model> --model gemini/<model>
```

`--deep-review` adds the L2 static review: LLM judges read the statement, tests, verifier command
and reference fix (first pass), then answer three questions with the reference hidden
(can every tested requirement be known, do the tests enforce the request, could a trivial
solution pass), three independent votes each. Tests often check edge cases the statement
never spells out, so the determinability judges also see where the pre-change repository
shows each tested detail the statement doesn't name (an existing error message, constant or
sibling function, found by a plain text search with the tests themselves excluded); a
detail the repository explains is not a gap. A negative counts only if its quoted assertion
exists verbatim in the files and its gap names something concrete; any valid negative
flags the task (needs your judgment until L3 runs it). It shows the number of calls and the
estimated cost and asks before spending (`--yes` skips the question). Judges from the
family of the model that wrote the task are left out; a call that fails is shown as
unfinished, never as passed. Answers are cached, so a re-check costs nothing.

On a full run, `--deep-review` also runs the L3 agentic audits: an agent with read-only tools over
the pre-change repository and a sandbox to run its own implementations in.

- **Too weak?** Seeing only the statement and the repository, the agent records every
  testable requirement (quoted), then writes wrong or partial implementations and grades
  them. RG110 is proven when the verifier passes one, the agent's own check shows the
  requirement broken, and the reference fix passes that same check.
- **Too strict?** Allowed to read the tests, the agent writes a complete alternative that
  follows the statement but differs where it is silent. RG408 is proven when the verifier
  fails it only on those tests and the agent's check of the statement passes on it and on
  the reference.
- L2's weak-pass shortcuts are run before a task is rejected (passed → RG110, rejected →
  dropped); L2's determinability gaps are dropped when the repository answers them (with a
  verbatim quote). Every such outcome is logged, and the report shows how often L2's claims
  held up when execution checked them.

The agent navigates large repositories by structure: a symbol index built once per
repository content (`outline`, `find_symbol`, `read_symbol`, `find_refs`, 120-line reads),
a starting outline of the files the statement and tests point at, and old tool outputs
replaced by a one-line placeholder once more than 14 are in the conversation (in batches, so
the provider's prompt cache keeps working). Verdicts of identical candidates are cached.

On a full run with `--backend docker` or `daytona`, `--deep-review` also runs the **L4 hack
agent**: three short runs, each given one slice of the reward-hack taxonomy (tamper with
the grading setup; wrong code that passes; find the solution), with a shell in their own
container, the statement, and never the tests or the reference. Nothing it claims is taken
on its word:

- every submission replays all its commands in a fresh container and runs the real grader;
- a pass that needs more than its source changes (graded alone on another fresh copy) is a
  forged pass through the grading setup: RG215, proven;
- a pass with source changes only is proven wrong code (RG111) when the agent's check
  command fails on it and passes on the reference fix; otherwise a person reads it (RG113);
- a command that prints the reference fix's added lines on a fresh copy is RG308, proven.

A saved hack is replayed first on the next check; while it still works the agent isn't
re-run. The hack agent never runs on the local backend.

**Rubric-graded tasks** (a `rubric.md|json|yaml` beside the task): the rubric is checked
for atomic, observable, distinct, weighted criteria that cover the statement (RG409-RG411),
and with `--deep-review` a white-box attacker (sees the rubric) and a black-box attacker (sees only
the task) write cheap deliverables that a rubric judge and a goal judge (who never sees the
rubric) grade independently; a deliverable that passes the rubric but misses the goal is
RG112.

Each proof comes with `rlgate repro`. Limits: `[models] agent`, `agent_turns` (30),
`agent_grades` (6; the hack agent gets at most 3 submissions per run). All of it uses only
the models you configure, one pass each: nothing escalates to a costlier model on its own.

To measure what the checks find, `rlgate selftest DIR` writes tasks with one planted
defect each (and a clean one), checks them, and shows which defects were found and what
fired where it shouldn't. Add `--deep-review` (most planted defects need the LLM review).

**Settings work from any folder.** rlgate reads `rlgate.toml` files in layers, and each
layer changes only the keys it sets:

1. your own `~/.config/rlgate/rlgate.toml` (`rlgate init --user` writes one);
2. the nearest `rlgate.toml` above the folder you run in;
3. the nearest one above the task you check (a bank's settings apply wherever you run
   from);
4. `--config PATH` or `RLGATE_CONFIG` (replaces 2 and 3), then `RLGATE_*` variables, then
   flags.

Keys go in a `.env` beside any of those files, or in `~/.config/rlgate/.env`; the nearest
wins, and the shell beats them all. `rlgate doctor` shows the files and keys it found.

```toml
# rlgate.toml — CLI flags > RLGATE_* env vars > this file > defaults
[environment]
type = "daytona"
cpus = 2
memory_gb = 4

[models]
review = ["openai/<model>", "gemini/<model>", "anthropic/claude-opus-5-5"]  # --deep-review judges
votes = 3                           # independent calls per review question
author = "anthropic/<model>"        # wrote the tasks: judges of its family are left out
budget_usd = 5.0                    # stop the LLM review at this spend
min_confidence = 0.5                # weaker judge negatives are discarded
agent = "openai/<model>"            # the L3 agent (empty: the first review judge)
```

**One OpenRouter key** works for every judge: name the models `openrouter/<maker>/<model>`
and set `OPENROUTER_API_KEY`. The family rule goes by the maker, so
`openrouter/openai/gpt-4o` counts as openai and two makers behind OpenRouter don't clash.
Each call's real cost comes back from OpenRouter, so spend and `budget_usd` work. The
estimate before the run needs LiteLLM's price table, which lacks many OpenRouter models.
For those it says "cost unknown" (never $0) until you give the price, in LiteLLM's
`model_info` keys as in Harbor:

```toml
[models]
review = ["openrouter/openai/gpt-4o", "openrouter/google/gemini-2.5-pro"]
agent = "openrouter/moonshotai/kimi-k2"

[models.info."openrouter/moonshotai/kimi-k2"]   # dollars per token
input_cost_per_token = 0.0000006
output_cost_per_token = 0.0000025
supports_function_calling = true
```

## Status

Built: the six-step check (quick and full) with resume and `-x` to stop early, the L0
build gates, 76 rules across the six steps, proofs you can re-run, waivers, the fix loop, the
bank view, JSON / Markdown QA card / SARIF / HTML output, and the L2 static review behind
`--deep-review`, the L3 agentic audits, the L4 hack agent and rubric attacks, and the
planted-defect bank. Not built yet: dedup and calibration (L5). Contributions welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md), and [CHANGELOG.md](CHANGELOG.md) for what changed.

MIT licensed.
