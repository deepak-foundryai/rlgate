# rlgate

**Find reward-hacking holes in RL training environments before you train on them.**

Point rlgate at a coding task (Harbor / Terminal-Bench, SWE-bench, MiMo, or your own layout)
and it answers three questions: is the task OK, what is wrong, and how do you fix it.

Every finding is one of two kinds:

- **Proven**: rlgate ran it and showed the problem, and gives you a command to reproduce it.
- **Needs your judgment**: a real signal that a person should decide on.

The standard checks are deterministic and use no LLM. An optional LLM review
(`--deep-review`) adds judges and agents on top.

```text
$ rlgate demo
rlgate check ./rate-limiter                             quick run · 0.0s

 Step 1  Build     ✓ partly checked  2 checks clean; the rest needs rlgate demo --full
 Step 2  Baseline  ? to review       1 item needs your judgment
 Step 3  Leaks     ✗ failed          patch leftover: app/limiter.py.orig
 ...

 RG303  Patch leftover files contain the fix   PROVEN · high
   What:  app/limiter.py.orig — holds pre/post-patch content verbatim
   Fix:   Remove *.orig / *.rej before shipping the task.
   More:  rlgate explain RG303 ./rate-limiter
```

## Install

```bash
uv tool install 'git+https://github.com/deepak-foundryai/rlgate'
# optional extras: 'rlgate[daytona]' for Daytona sandboxes, 'rlgate[llm]' for --deep-review
```

Python 3.12+. Full runs need Docker or a Daytona account; `--quick` needs neither.

## Quick start

```bash
rlgate demo                      # a deliberately broken task: real findings in seconds
rlgate doctor                    # what this machine can run, and how to fix what's missing
rlgate check ./my-task           # one task
rlgate check ./tasks -j 8        # every task in a folder, 8 at a time
rlgate explain RG303 ./my-task   # one finding in full: What, Why, Proof, Fix
```

## What it checks

`rlgate check` runs six steps in order:

| Step | Question | What it looks at | Rules |
| --- | --- | --- | --- |
| 1 Build | Can the task be built and run? | Layout, Dockerfiles (pinning, layer order, broad copies), downloads at grading time, every image builds | RG6xx |
| 2 Baseline | Does the task work? | The reference solution passes and doing nothing fails, 5 runs each from scratch; fail-to-pass and pass-to-pass tests; nothing outside the submission reaches the grader; time and resource limits | RG0xx |
| 3 Leaks | Is the answer hidden from the agent? | Solution or tests in the agent's image, git history and leftover git objects, caches, patch leftovers, late-added files, internet access | RG3xx |
| 4 Grader | Does the grader really check the fix? | Remove each part of the fix, stub each changed function, mutate it, hardcode the visible answers; reward wiring | RG1xx, RG5xx |
| 5 Cheating | Can a pass be faked? | Exit 0 early, fake reports, always-equal objects, `conftest.py` / `sitecustomize` hooks, fake clocks, changed test selection, editable test data | RG2xx |
| 6 Fairness | Are the task and tests clear and fair? | Overfit wording, internal names the task never mentions, brittle assertions | RG4xx |

There are 76 rules in total, listed in [docs/rules.md](docs/rules.md) or by `rlgate explain`.

- **Stopping:** a broken build or baseline stops the run, because nothing after it could
  be trusted. Steps 3-6 all run even when one fails, so you see every problem at once; `-x`
  stops at the first failing step instead.
- **Re-runs:** a re-check reuses the saved results of steps whose files didn't change.
- **`--quick`:** runs only the file-only part of each step, in seconds and without Docker.

## Commands

| Command | What it does |
| --- | --- |
| `rlgate check <task or folder>` | Check one task, or every task in a folder, and give a verdict |
| `rlgate explain [rule] [task]` | List every rule; explain one with examples; or show one finding in full |
| `rlgate repro <rule> <task>` | Re-run the proof of a proven finding |
| `rlgate waive <rule> <task> -r "reason"` | Accept a finding and record why (`--list`, `--remove`) |
| `rlgate report` | Re-render the last result as html, markdown, json, sarif or badge |
| `rlgate doctor [task]` | Check this machine, keys and backends (`--ping` makes one real call to each) |
| `rlgate init` | Write a starter `rlgate.toml` (`--user` for your personal settings) |
| `rlgate demo` | Check the bundled broken task (`--full` also grades it) |

### `rlgate check` options

| Option | What it does |
| --- | --- |
| `--only STEPS` | Only these steps (`leaks,grader` or `3,4`), or a single rule such as `RG301` |
| `--from STEP` | Start at a step; earlier steps come from the last run |
| `--failed` | Re-run only what failed last time |
| `--fresh` | Ignore saved results |
| `--limit N` | At most N tasks of a folder |
| `--quick` | File-only checks: seconds, no Docker |
| `--deep-review` | Add the LLM review (below); prints the total cost at the end |
| `--model M` | Judge model for `--deep-review`; repeat it for a panel |
| `--exhaustive` | Grade every mutation instead of skipping ones that can't add a finding |
| `--runs N` | Grading runs of the reference and do-nothing submissions (default 5) |
| `-j N` | Check N tasks at once |
| `--backend` | `auto`, `docker`, `daytona` or `local` |
| `--backend-opt k=v` | A backend setting, such as `cpus=2` |
| `-x` | Stop at the first failing step |
| `-f FORMAT`, `-o FILE` | Output format, or write to a file (format from the extension) |
| `--layout NAME or FILE` | Task layout when it isn't detected |

**Exit codes:** 0 ready, 1 needs fixes, 2 broken, not checked, or could not run.

## The fix loop

```bash
rlgate check ./bank -j 8
rlgate explain RG305 ./bank/task-x           # what, why, proof, fix
rlgate repro RG305 ./bank/task-x             # watch it happen
# edit the task, or accept the finding:
rlgate waive RG305 ./bank/task-x -r "files are generated at build time"
rlgate check ./bank -j 8                     # unchanged steps come from the last run
rlgate report -o report.html                 # a filterable report to share
```

Waivers are stored in `rlgate.toml` next to the task and show on every report as
"accepted, with reason". They come back automatically when the files they refer to change.
Proven findings of high severity can't be waived, only fixed.

## Task formats

These layouts are detected automatically:
- **Harbor** (`task.toml`, `instruction.md`, `environment/`, `tests/`, `solution/`):
  Terminal-Bench 2 and anything packaged like it. rlgate reads it natively and grades it
  the way the harness does, including a separate verifier environment and compose stacks.
- **SWE-bench** rows (`.jsonl`).
- **MiMo**.

For anything else:
- **A preset:** `--layout deepswe`, for example.
- **A short YAML file:** describe the layout, no code needed.

```yaml
# task.rlgate.yaml
workspace: ./repo
gold_patch: ./solution/fix.patch          # optional: enables the grader checks (step 4)
grader:
  run: "bash run_tests.sh"
  report: {format: junit, path: /out/results.xml}
  tests: [{from: ./hidden, to: .graded}]  # optional held-out tests, copied in only to grade
  verdict: exit_code                      # or report | both | reward_file
```

Checks that need something a layout doesn't provide are shown as "doesn't apply", never
as passed.

## Where tasks run

| Backend | Setup | Use it for |
| --- | --- | --- |
| `docker` | A running Docker daemon | Local full runs; one container per grade |
| `daytona` | `rlgate[daytona]` and `DAYTONA_API_KEY` | Large banks; each image is built once into a cached snapshot |
| `local` | Nothing | Fixtures and development; no isolation |

`auto` (the default) picks Daytona when it's configured, then Docker, then local.

On Daytona:
- **Sandboxes:** every sandbox rlgate makes is labelled, deleted when the check ends, and
  has an auto-stop and a time limit, so a crashed run can't leave machines billing.
- **Account limits:** rlgate reads your plan's limits (snapshots, and CPU, memory and disk
  per region) and works within them. To free a snapshot slot it deletes only idle
  `rlgate-*` snapshots it made itself, never yours.
- **Infrastructure errors:** a task the backend failed on is reported as **Not checked**.
  Run the same command again without `--fresh` and only those tasks re-run.

## The LLM review (`--deep-review`)

Optional, and it uses only the models you configure, one pass each. It never escalates to
a costlier model on its own. Any LiteLLM `provider/model` works, and one OpenRouter key can
serve every judge.

```bash
pip install 'rlgate[llm]'
export OPENROUTER_API_KEY=...
rlgate check ./task --deep-review --model openrouter/openai/gpt-4o --model openrouter/google/gemini-2.5-pro
```

It runs in three layers:

- **L2, judges:** LLM judges read the statement, tests and verifier, then answer: can every
  tested requirement be known from what the solver sees, do the tests enforce the request,
  and could a trivial solution pass. Each question gets three independent votes.
  - A "no" counts only if it quotes the test verbatim.
  - Judges from the family of the model that wrote the task are left out.
- **L3, agent audits:** an agent explores the pre-change repository, writes wrong and
  alternative implementations, and grades them with the real verifier. It proves the
  grader too weak (RG110: a wrong implementation passes) or too strict (RG408: a correct
  alternative fails).
- **L4, hack agent:** short runs that try to tamper with grading, pass with wrong code, or
  find the solution in the environment. Every claimed hack is replayed in a fresh container
  against the real grader before it's reported. This needs Docker or Daytona.

Answers are cached, so re-checking a task costs nothing. When the run ends, rlgate prints
the calls, tokens and total cost. To set a spending cap, add `budget_usd` under `[models]`;
there is none by default.

## Settings

rlgate reads `rlgate.toml` files in layers. Each layer changes only the keys it sets, and
later layers win:

1. `~/.config/rlgate/rlgate.toml`, your personal settings (`rlgate init --user` writes it);
2. the nearest `rlgate.toml` above the folder you run in;
3. the nearest one above the task you check;
4. `--config PATH`, then `RLGATE_*` environment variables, then command-line flags.

API keys can go in a `.env` beside any of these files. `rlgate doctor` shows what it found.

```toml
[environment]
type = "daytona"
cpus = 2
memory_gb = 4

[checks]
workers = 4                       # grading runs at once inside a task

[models]
review = ["openrouter/openai/gpt-4o", "openrouter/google/gemini-2.5-pro"]
agent = "openrouter/openai/gpt-4o"   # the L3 agent (default: the first judge)
votes = 3
# budget_usd = 5.0                # optional spending cap

[models.info."openrouter/moonshotai/kimi-k2"]   # price for a model LiteLLM doesn't know
input_cost_per_token = 0.0000006
output_cost_per_token = 0.0000025
```

## In CI

There's a GitHub Action and a pre-commit hook; see
[docs/github-action.md](docs/github-action.md).

## Status

Already built:
- the six-step check (quick and full) with its 76 rules and re-runnable proofs;
- waivers;
- the bank view;
- JSON, Markdown, SARIF, HTML and badge output;
- the L2/L3/L4 LLM review;
- a planted-defect self-test.

Not yet built: dedup and calibration.

See [CHANGELOG.md](CHANGELOG.md) for what changed (including renamed commands and flags)
and [CONTRIBUTING.md](CONTRIBUTING.md) to help. MIT licensed.
