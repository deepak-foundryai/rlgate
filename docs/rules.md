# rlgate rule catalogue

Every finding has a stable rule ID. `rlgate check` runs six numbered steps in order and stops at the first one with a proven problem; each rule belongs to one step. Generated from the code by `python -m rlgate.core.rule_docs`; `rlgate explain <rule>` shows the same in the terminal.

| Step | Name | Question | Rules |
| --- | --- | --- | --- |
| 1 | Build | Can the task be built and run? | RG6xx |
| 2 | Baseline | Does the task work? | RG0xx |
| 3 | Leaks | Is the answer hidden from the agent? | RG3xx |
| 4 | Grader | Does the grader really check the fix? | RG1xx, RG5xx |
| 5 | Cheating | Can a pass be faked? | RG2xx |
| 6 | Fairness | Are the task and tests clear and fair? | RG4xx, RG7xx |

Optional, after all six steps pass: difficulty (RG8xx, `rlgate calibrate`) and duplicates across a bank (RG9xx). Not checked yet.

## Step 1: Build. Can the task be built and run?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG601](#rg601) | COPY/ADD pulls the whole build context into the image | Needs your judgment | high |
| [RG602](#rg602) | The base image is not pinned | Needs your judgment | medium |
| [RG603](#rg603) | The build depends on the host's CPU count | Needs your judgment | medium |
| [RG604](#rg604) | A dependency is given as a version range | Needs your judgment | low |
| [RG606](#rg606) | Dependencies install after the source is copied | Needs your judgment | low |
| [RG607](#rg607) | The grader downloads software while grading | Needs your judgment | low |
| [RG608](#rg608) | An image the task uses does not build | Proven | critical |
| [RG605](#rg605) | The task can't run the way its harness runs it | Proven | medium |

### RG601

**COPY/ADD pulls the whole build context into the image** · Needs your judgment · high

COPY . (or ADD .) puts the whole build context into the image — including tests or solutions that sit next to the Dockerfile.

**Why it matters:** A broad copy can drag held-out tests or the reference solution into the agent image.

Bad:

```dockerfile
COPY . /app
```

Good:

```dockerfile
COPY src/ /app/src/
COPY requirements.txt /app/
```

**Fix:** Copy only the needed paths; keep tests in a separate verifier image.

### RG602

**The base image is not pinned** · Needs your judgment · medium

The base image has no fixed version, so rebuilding later can give a different environment.

**Why it matters:** An untagged or :latest base changes under you; builds aren't reproducible.

Bad:

```dockerfile
FROM python
```

Good:

```dockerfile
FROM python:3.12.7-slim   # or @sha256:<digest>
```

**Fix:** Pin the base image by tag or @sha256 digest.

### RG603

**The build depends on the host's CPU count** · Needs your judgment · medium

The build uses the host's CPU count, so images differ between machines.

**Why it matters:** nproc varies by host, so build and test parallelism are non-deterministic.

Bad:

```dockerfile
RUN make -j$(nproc)
```

Good:

```dockerfile
RUN make -j4
```

**Fix:** Hardcode the parallelism or derive it deterministically.

### RG604

**A dependency is given as a version range** · Needs your judgment · low

A dependency is installed by range, so the version depends on when the image was built.

**Why it matters:** A range resolves differently over time, so builds aren't reproducible.

Bad:

```dockerfile
RUN pip install 'requests>=2'
```

Good:

```dockerfile
RUN pip install requests==2.32.3   # or a locked requirements file
```

**Fix:** Pin exact versions (pkg==x.y.z) or use a locked requirements file.

### RG606

**Dependencies install after the source is copied** · Needs your judgment · low

A dependency install comes after a COPY of the task's source, so Docker can't reuse the install layer when the source changes.

**Why it matters:** Every change to the task's files re-runs the dependency install, so images rebuild slowly and can pick up different versions each time.

Bad:

```dockerfile
COPY . /app
RUN pip install -r requirements.txt
```

Good:

```dockerfile
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY . /app
```

**Fix:** Copy only the dependency manifests (requirements.txt, package.json, …) and install them before copying the rest of the source.

### RG607

**The grader downloads software while grading** · Needs your judgment · low

The grader's script installs or downloads software every time it runs.

**Why it matters:** A grader that installs packages at grading time needs open network and can get different versions on different runs.

Bad:

```dockerfile
# tests/test.sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uvx pytest /tests
```

Good:

```dockerfile
# tests/Dockerfile
RUN pip install pytest==8.3.3
# tests/test.sh
pytest /tests
```

**Fix:** Install the grader's tools in the verifier image at build time, pinned.

### RG608

**An image the task uses does not build** · Proven · critical

Every image the task uses (the agent's environment and a separate verifier, including compose services) is built. If one fails, the task can't run at all.

**Why it matters:** A task whose environment can't be built can't be trained on at all.

Bad:

```dockerfile
RUN pip install -r requirements.txt   # requirements.txt pins a yanked version
```

Good:

```dockerfile
RUN pip install -r requirements.txt   # every pin resolves; the image builds
```

**Fix:** Fix the build error shown in the finding, then run the check again.

### RG605

**The task can't run the way its harness runs it** · Proven · medium

The task's files don't describe something its harness can run as written: a missing statement or reference solution, a test script that never writes a reward, or a shape (multi-step, Windows, sidecar artifacts) rlgate can't reproduce.

**Why it matters:** A task that its own harness can't run, or runs differently, trains on a different task than the one that was checked.

Bad:

```dockerfile
# tests/test.sh
pytest /tests   # exit code only; no reward file written
```

Good:

```dockerfile
# tests/test.sh
pytest /tests && echo 1 > /logs/verifier/reward.txt \
  || echo 0 > /logs/verifier/reward.txt
```

**Fix:** Fix the task files named in the finding so the harness can run the task as written.

## Step 2: Baseline. Does the task work?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG001](#rg001) | The reference solution does not pass | Proven | critical |
| [RG002](#rg002) | Doing nothing passes | Proven | critical |
| [RG003](#rg003) | The grade is flaky | Proven | critical |
| [RG004](#rg004) | The grader runs in the agent's environment | Needs your judgment | medium |
| [RG006](#rg006) | No test goes from failing to passing with the fix | Proven | high |
| [RG007](#rg007) | The reference fix breaks a test that passed before | Proven | high |
| [RG008](#rg008) | No existing tests guard against regressions | Needs your judgment | low |
| [RG009](#rg009) | The fix doesn't survive the trip to the grader | Proven | critical |
| [RG010](#rg010) | Changes outside the submission reach the grader | Proven | high |
| [RG011](#rg011) | The task runs too close to its time limit | Proven | high |
| [RG012](#rg012) | The task doesn't declare its resource limits | Needs your judgment | low |

### RG001

**The reference solution does not pass** · Proven · critical

The grader is run on the task's own reference solution. If that doesn't score full marks every time, no agent can be graded fairly against it.

**Why it matters:** If the gold fix can't earn full reward, every reward signal on this task is noise.

Bad:

```python
# tests expect 'total: 3' but solution/patch.diff prints 'Total: 3'
reward = 0.0 for the reference solution
```

Good:

```python
# the reference solution and the tests agree
reward = 1.0 for the reference solution, 5 runs out of 5
```

**Fix:** Fix the reference solution or the grader until gold scores 1.0 on every run.

### RG002

**Doing nothing passes** · Proven · critical

The grader is run on the task exactly as shipped. If doing nothing passes, the reward teaches the agent that no work is needed.

**Why it matters:** The model is rewarded without changing anything, so it learns to do nothing.

Bad:

```python
def test_runs():
    import app          # passes before any fix
```

Good:

```python
def test_rejects_negative_amount():
    assert app.charge(-5).status == 400
```

**Fix:** Make the grader fail on the unchanged workspace (add a test the fix makes pass).

### RG003

**The grade is flaky** · Proven · critical

The same submission is graded several times. A grade that changes between identical runs is noise in the reward.

**Why it matters:** The same submission gets different rewards, so the reward is partly random.

Bad:

```python
assert elapsed < 0.05        # depends on machine load
```

Good:

```python
assert result == expected    # deterministic; no timing, no network
```

**Fix:** Remove nondeterminism (timing, ordering, network, randomness) from the flaky tests.

### RG004

**The grader runs in the agent's environment** · Needs your judgment · medium

The agent's work is graded inside the same container the agent used, instead of a fresh verifier that only receives the agent's output.

**Why it matters:** Anything the agent leaves behind (processes, files outside the workspace, patched tools) is still there when its work is graded, so it can sway the grade.

Bad:

```python
[verifier]
environment_mode = "shared"   # tests run where the agent ran
```

Good:

```python
artifacts = ["/app"]
[verifier]
environment_mode = "separate"
```

**Fix:** Grade in a separate verifier environment that receives only the agent's declared output (Harbor: [verifier] environment_mode = "separate" with artifacts).

### RG006

**No test goes from failing to passing with the fix** · Proven · high

The grader's per-test report is read before and after the reference fix. At least one test must fail on the unchanged task and pass with the fix (fail-to-pass).

**Why it matters:** Without a fail-to-pass test, nothing in the grader shows the fix is what earns the reward.

Bad:

```python
def test_app_imports():
    import app      # passes before and after the fix
```

Good:

```python
def test_refund_is_capped():
    assert refund(order, 999).amount == order.total
```

**Fix:** Add a test that fails on the unchanged task and passes with the reference fix.

### RG007

**The reference fix breaks a test that passed before** · Proven · high

A test that passes on the unchanged task fails once the reference fix is applied. Either the fix breaks behaviour, or the grade ignores that test.

**Why it matters:** A test that the fix breaks is either wrong or ignored by the grade; either way, regressions aren't rejected.

Bad:

```python
before the fix: test_list_users passes
after the fix:  test_list_users fails, reward 1.0
```

Good:

```python
before and after the fix: test_list_users passes
```

**Fix:** Fix the test or the reference solution so every test that passed before still passes.

### RG008

**No existing tests guard against regressions** · Needs your judgment · low

No test passes both before and after the fix (pass-to-pass), so nothing checks that existing behaviour still works.

**Why it matters:** With no pass-to-pass tests, a solution that breaks unrelated behaviour still earns full reward.

Bad:

```python
pytest /tests/test_new_feature.py        # only the new behaviour is graded
```

Good:

```python
pytest /tests/test_new_feature.py /tests/test_existing.py
```

**Fix:** Grade the existing tests too, so they must keep passing after the change.

### RG009

**The fix doesn't survive the trip to the grader** · Proven · critical

The reference solution is run, its changes to the workspace are extracted as a patch, and that patch alone is applied to a fresh environment and graded, the way training grades an agent's submission.

**Why it matters:** Training extracts the agent's changes as a patch and grades that. If the reference only passes when run in place, the reward training sees differs from the one checked.

Bad:

```python
# solve.sh
pip install fixed-lib==2.1   # outside the workspace; lost in the patch
```

Good:

```python
# solve.sh
echo 'fixed-lib==2.1' >> requirements.txt   # part of the patch
```

**Fix:** Make the reference solution change only files inside the workspace (or the declared artifacts), so its patch alone passes.

### RG010

**Changes outside the submission reach the grader** · Proven · high

A run writes marker files in /tmp and the home directory and leaves a background process, then checks whether any of them are visible when grading starts.

**Why it matters:** Files, packages or processes the agent leaves behind outside its submission are still there when it is graded, so they can sway the grade.

Bad:

```python
[verifier]
environment_mode = "shared"   # /tmp and processes survive into grading
```

Good:

```python
artifacts = ["/app"]
[verifier]
environment_mode = "separate"
```

**Fix:** Grade in a fresh verifier environment that receives only the declared artifacts (Harbor: [verifier] environment_mode = "separate").

### RG011

**The task runs too close to its time limit** · Proven · high

Each reference and grading run is timed against the task's own limits. A run near its limit times out on a slower machine.

**Why it matters:** A run that hits its timeout scores 0 for reasons that have nothing to do with the solution, so the reward is noise.

Bad:

```python
[verifier]
timeout_sec = 60     # the grader takes 55s
```

Good:

```python
[verifier]
timeout_sec = 300    # the grader takes 55s
```

**Fix:** Speed up the grader or the solution, or raise timeout_sec in task.toml.

### RG012

**The task doesn't declare its resource limits** · Needs your judgment · low

task.toml doesn't say how much CPU, memory or time the task gets, so the checked conditions and the training conditions can differ.

**Why it matters:** Without declared CPU, memory and time limits, training runs it under whatever defaults apply, which may differ from what was checked.

Bad:

```python
[environment]
build_timeout_sec = 600
```

Good:

```python
[environment]
cpus = 2
memory_mb = 4096
[agent]
timeout_sec = 1800
[verifier]
timeout_sec = 300
```

**Fix:** Declare [environment] cpus / memory_mb and [agent] / [verifier] timeout_sec in task.toml.

## Step 3: Leaks. Is the answer hidden from the agent?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG301](#rg301) | The fix is recoverable from git objects | Proven | high |
| [RG302](#rg302) | Compiled code of a deleted file remains | Proven | high |
| [RG303](#rg303) | Patch leftover files contain the fix | Proven | high |
| [RG304](#rg304) | A file is named like an answer | Needs your judgment | medium |
| [RG305](#rg305) | Some files were added after checkout | Needs your judgment | medium |
| [RG306](#rg306) | The reference solution or tests are inside the agent's environment | Proven | critical |
| [RG307](#rg307) | The agent has unrestricted internet access | Needs your judgment | low |
| [RG308](#rg308) | The hack agent found the solution in the environment | Proven | critical |

### RG301

**The fix is recoverable from git objects** · Proven · high

The fix exists in the repository's git objects (an unreachable commit, a stash, a packed object) even though the working tree is unsolved.

**Why it matters:** The agent can read the reference solution out of the git object store.

Bad:

```python
git checkout base && rm -rf solution   # the fix commit is still in .git
```

Good:

```python
fresh clone at the base commit, or git gc --prune=now after removing refs
```

**Fix:** Rebuild the repo without the fix's objects (fresh clone at the base commit, gc).

### RG302

**Compiled code of a deleted file remains** · Proven · high

A compiled .pyc remains for a source file that was removed — it can be decompiled back into the code.

**Why it matters:** Bytecode of removed source can be decompiled back into the answer.

Bad:

```python
app/__pycache__/solution.cpython-312.pyc   # solution.py was deleted
```

Good:

```python
build with PYTHONDONTWRITEBYTECODE=1 and remove __pycache__ before shipping
```

**Fix:** Ship the task without __pycache__; delete bytecode whose source was removed.

### RG303

**Patch leftover files contain the fix** · Proven · high

A *.orig or *.rej file left by applying a patch holds the before/after code — the answer, sitting next to the file the agent must fix.

**Why it matters:** .orig / .rej files hold pre- or post-patch content verbatim.

Bad:

```python
app/calc.py
app/calc.py.orig   # written by `patch`, contains the fixed code
```

Good:

```python
find . -name '*.orig' -o -name '*.rej' | xargs rm -f   # before building
```

**Fix:** Remove *.orig and *.rej files before shipping the task.

### RG304

**A file is named like an answer** · Needs your judgment · medium

A file is named like an answer key (solution, expected_output, golden). It may be harmless, but if it holds the answer the agent will find it.

**Why it matters:** Reference or expected output may be readable by the agent.

Bad:

```python
app/expected_output.txt   # the exact output the tests compare against
```

Good:

```python
keep reference outputs in tests/ (uploaded only when grading)
```

**Fix:** Move reference material out of the agent's tree, or rename it if benign.

### RG305

**Some files were added after checkout** · Needs your judgment · medium

A few files have much later modification times than the rest of the checkout — often where the task's changes (or the answer) were added.

**Why it matters:** `ls -lt` shows the agent which files were injected (tests, solution, answer key).

Bad:

```python
every file 2024-01-01 except app/billing.py 2026-11-09
```

Good:

```python
normalise mtimes when building (e.g. touch -d @0 on every file)
```

**Fix:** Normalise modification times across the workspace when building the image.

### RG306

**The reference solution or tests are inside the agent's environment** · Proven · critical

The agent's environment is searched for files identical to the task's solution/ and tests/ files.

**Why it matters:** The agent can read the answer or the held-out tests straight off disk.

Bad:

```python
COPY . /app        # copies solution/patch.diff into the image
```

Good:

```python
COPY src/ /app/src/   # solution/ and tests/ never enter the agent image
```

**Fix:** Keep solution/ and tests/ out of the agent image; the harness copies tests in only at grading time.

### RG307

**The agent has unrestricted internet access** · Needs your judgment · low

The agent's environment allows any outbound traffic, so the upstream fix is one download away.

**Why it matters:** With open internet the agent can download the upstream fix instead of writing it.

Bad:

```python
[environment]
# allow_internet not set: open internet
```

Good:

```python
[environment]
allow_internet = false   # or a mirror with an allow list
```

**Fix:** Set allow_internet = false, or route traffic through package mirrors with an allow list and request logging.

### RG308

**The hack agent found the solution in the environment** · Proven · critical

A hack run reported a command that reveals the solution. It is re-run on a fresh copy of the environment; proven when its output contains the reference fix's added lines.

**Why it matters:** A command the agent can run prints the reference fix, so the model can copy the answer instead of writing it.

Bad:

```python
git log --all -p | grep -A20 'def charge'   # prints the reference fix
```

Good:

```python
the image is built from a clean checkout of the pre-change commit, with no history past it and no caches
```

**Fix:** Remove the source the command reads (history, caches, files, network access) from the image the agent runs in.

## Step 4: Grader. Does the grader really check the fix?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG101](#rg101) | A part of the fix is not tested | Proven | high |
| [RG102](#rg102) | A changed function can be replaced by a stub | Proven | high |
| [RG103](#rg103) | A small change to the fix goes unnoticed | Needs your judgment | medium |
| [RG104](#rg104) | Hardcoded answers to the visible tests pass | Proven | high |
| [RG105](#rg105) | Part of the reference fix is unrelated to the task | Needs your judgment | medium |
| [RG106](#rg106) | The reference fix hardcodes answers or edits the tests | Needs your judgment | critical |
| [RG107](#rg107) | The grading command can pass without the tests passing | Needs your judgment | critical |
| [RG108](#rg108) | A requirement in the task is not enforced by any test | Needs your judgment | high |
| [RG109](#rg109) | A trivial or incomplete solution could pass | Needs your judgment | high |
| [RG110](#rg110) | An incomplete or wrong implementation passes | Proven | high |
| [RG111](#rg111) | The hack agent passed with wrong code | Proven | critical |
| [RG113](#rg113) | The hack agent passed with code it says is wrong | Needs your judgment | high |
| [RG112](#rg112) | A deliverable that misses the goal earns rubric credit | Needs your judgment | high |
| [RG501](#rg501) | A held-out signal never reaches the training reward | Proven | high |
| [RG502](#rg502) | Milestone weights are defined but unused | Proven | medium |
| [RG503](#rg503) | A weight differs across scoring files | Proven | medium |
| [RG504](#rg504) | A multiplicative gate can zero the whole reward | Proven | medium |
| [RG505](#rg505) | A participation term depends on feature success | Proven | medium |
| [RG506](#rg506) | The reward-hacking heuristic points the wrong way | Proven | high |
| [RG507](#rg507) | Scoring files contradict each other | Proven | medium |

### RG101

**A part of the fix is not tested** · Proven · high

Each part (hunk) of the reference fix is removed in turn. If the tests still pass without it, that part of the fix is never checked, and an agent can skip it.

**Why it matters:** A solution that skips this part of the fix still earns full reward.

Bad:

```python
fix: validates input AND logs the error
tests: only check the validation
```

Good:

```python
tests: one test per behaviour the fix adds — including the logging
```

**Fix:** Add a test that fails when this hunk of the fix is missing.

### RG102

**A changed function can be replaced by a stub** · Proven · high

Each function the reference fix changes is replaced in turn by a stub that returns None or raises. If the tests still pass, nothing checks that function.

**Why it matters:** A solution that doesn't implement this function at all still earns full reward.

Bad:

```python
def apply_discount(order):
    return None      # stub: tests still pass
```

Good:

```python
def test_discount_applied():
    assert apply_discount(order).total == 90
```

**Fix:** Add a test that calls this function and checks what it returns or does.

### RG103

**A small change to the fix goes unnoticed** · Needs your judgment · medium

Small mutations of the fix (flip a comparison, change a constant) are graded. If one still passes, the tests can't tell the right behaviour from a near miss.

**Why it matters:** A subtly wrong solution may earn full reward; it may also be a harmless (equivalent) change.

Bad:

```python
if retries > 3:    # mutation to >= 3 still passes
```

Good:

```python
assert attempts_before_giving_up() == 3   # pins the boundary
```

**Fix:** Add an assertion that tells these behaviours apart, or waive it as equivalent.

### RG104

**Hardcoded answers to the visible tests pass** · Proven · high

The visible tests are run against the reference and every call to a changed function is recorded. The function is then replaced by a lookup table of those answers. If the graded tests still pass, they never use an input the agent couldn't see.

**Why it matters:** The agent can read the visible tests and return their expected outputs instead of solving the task.

Bad:

```python
ANSWERS = {(2,): 4, (3,): 9}     # copied from the visible tests
def square(x): return ANSWERS.get((x,))
```

Good:

```python
# held-out tests call square() with inputs that appear in no visible test
```

**Fix:** Grade with held-out tests whose inputs the agent never sees, covering more inputs than the visible tests use.

### RG105

**Part of the reference fix is unrelated to the task** · Needs your judgment · medium

With --deep-review, an LLM reads the statement and the reference fix. It flags a part of the fix that serves nothing the statement asks for, quoting that part.

**Why it matters:** The checks that compare against the reference (revert, stub, mutation) and the difficulty estimate count work the task never asked for.

Bad:

```python
statement: make /rsvp accept one answer per event
reference: ...and also renames every logger and bumps flask
```

Good:

```python
reference: only the per-event RSVP change the statement asks for
```

**Fix:** Remove the unrelated change from the reference fix, or ask for it in the statement.

### RG106

**The reference fix hardcodes answers or edits the tests** · Needs your judgment · critical

With --deep-review, an LLM reads the reference fix. It flags code that special-cases the tests' inputs or expected values, and edits to test files.

**Why it matters:** A reference that special-cases the tests or changes them proves nothing about the task, and teaches that gaming the tests is the solution.

Bad:

```python
def total(xs):
    if xs == [1, 2, 3]:      # the test's input
        return 6
```

Good:

```python
def total(xs):
    return sum(xs)
```

**Fix:** Rewrite the reference to solve the task in general and leave the test files alone; keep the task out of training until then.

### RG107

**The grading command can pass without the tests passing** · Needs your judgment · critical

With --deep-review, an LLM reads the grading command and the script it runs. It flags a reward that doesn't depend on the tests' result: success written regardless, failures swallowed, or a superficial check in place of the tests.

**Why it matters:** A verifier that manufactures success, swallows failures or checks something superficial rewards work that doesn't solve the task.

Bad:

```python
pytest /tests 2>/dev/null; echo 1 > /logs/verifier/reward.txt
```

Good:

```python
if pytest /tests; then echo 1; else echo 0; fi > /logs/verifier/reward.txt
```

**Fix:** Make the reward depend on the tests' own result; keep the task out of training until then.

### RG108

**A requirement in the task is not enforced by any test** · Needs your judgment · high

With --deep-review and the reference hidden, LLM judges compare each requirement in the statement with the tests. A requirement no test checks, or one checked so loosely that breaking it still passes, is flagged with the requirement quoted.

**Why it matters:** A solution can skip this requirement and still earn full reward.

Bad:

```python
statement: "one guest's RSVP must never affect another guest's"
tests: only ever use one guest
```

Good:

```python
tests: two guests RSVP to the same event; assert each keeps their own answer
```

**Fix:** Add a test that fails when this requirement is not met.

### RG109

**A trivial or incomplete solution could pass** · Needs your judgment · high

With --deep-review and the reference hidden, LLM judges look for a cheap implementation every test would accept: hardcoded values, special-cased inputs, half the change. The finding describes it; L3 confirms it by running it.

**Why it matters:** The cheapest way to the reward is a shortcut, so that is what training teaches.

Bad:

```python
def test_price():
    assert price(3, '0.10') == Decimal('0.30')   # the only case
```

Good:

```python
@pytest.mark.parametrize('qty,unit', [(3, '0.10'), (7, '1.15'), (0, '2.00')])
def test_price(qty, unit): ...
```

**Fix:** Add tests the shortcut fails: more inputs, held-out values, the part it skips.

### RG110

**An incomplete or wrong implementation passes** · Proven · high

With --deep-review on a full run, an agent that sees only the statement and the repository records the task's requirements, then writes implementations that break one and grades them. Proven when the verifier passes one, the agent's own check shows the requirement broken, and the reference fix passes that check.

**Why it matters:** An agent can skip or break a stated requirement and still earn full reward, so training teaches it to.

Bad:

```python
statement: double(x) returns x * 2 for any integer x
test: assert double(2) == 4      # 'return x + 2' passes too
```

Good:

```python
@pytest.mark.parametrize('x', [-3, 0, 2, 7])
def test_double(x): assert double(x) == 2 * x
```

**Fix:** Strengthen the tests so breaking this requirement fails them; otherwise leave the task out.

### RG111

**The hack agent passed with wrong code** · Proven · critical

With --deep-review on a container backend, short agent runs try to reach full reward without solving the task. Every pass is replayed in a fresh environment; then the source changes alone are graded. Proven when they pass AND the agent's check command fails on them and passes on the reference fix.

**Why it matters:** An agent told to cheat found wrong code that earns full reward, so training will find it too.

Bad:

```python
def is_prime(n):
    return n in (2, 3, 5, 7, 11)   # the tested inputs
# grader: reward 1.0
```

Good:

```python
@pytest.mark.parametrize('n', random_primes + random_composites)
def test_is_prime(n): ...
```

**Fix:** Add tests that the hack's code fails (its check command shows what to test), then re-run the task through every step; leave the task out until then.

### RG113

**The hack agent passed with code it says is wrong** · Needs your judgment · high

A hack run passed with source changes the agent says are wrong, but its check command didn't prove it (it passed on the hack too, or failed on the reference). The code is attached for a person to read.

**Why it matters:** The grader passed code an agent wrote to be wrong; the agent's own check didn't prove it, so a person should read the code.

Bad:

```python
claim: 'returns a cached value for the second call'
check: (none given)
```

Good:

```python
check: python3 -c "import m; assert m.f(2) != m.f(3)"  # fails on the hack
```

**Fix:** Read the attached code: if it is wrong, add tests it fails; if it is a correct solution, nothing to do.

### RG112

**A deliverable that misses the goal earns rubric credit** · Needs your judgment · high

For rubric-graded tasks, with --deep-review: one attacker that sees the rubric and one that sees only the task write cheap deliverables. A rubric judge and a goal judge grade each independently. Flagged when the rubric judge passes a deliverable the goal judge says misses the task's goal.

**Why it matters:** A rubric that credits work which doesn't achieve the task's goal teaches the model to satisfy the rubric's wording instead of doing the work.

Bad:

```python
criterion: 'The report includes a risk section'
deliverable: '## Risks\nThere are risks.'   # full credit
```

Good:

```python
criterion: 'The report names the two largest risks in the data and their size'
```

**Fix:** Add criteria (or penalty criteria) that the attack deliverable fails, and make every criterion check the goal, not the form.

### RG501

**A held-out signal never reaches the training reward** · Proven · high

A held-out check is scored for evaluation but never feeds the training reward, so training can't learn what it measures.

**Why it matters:** Held-out coverage is computed but gates nothing.

Bad:

```python
train = f2p_score
eval = f2p_score * held_out_score
```

Good:

```python
train = f2p_score * (0.5 + 0.5 * held_out_score)   # or gate on it
```

**Fix:** Route the held-out signal into the training reward, as a gate or a weighted term.

### RG502

**Milestone weights are defined but unused** · Proven · medium

Milestone weights are declared in the scoring config but the reward ignores them.

**Why it matters:** Cosmetic weights mislead anyone reading the reward spec.

Bad:

```python
"milestones": {"api": 0.6, "ui": 0.4}   # reward = mean(all checks)
```

Good:

```python
reward = 0.6 * api_score + 0.4 * ui_score
```

**Fix:** Apply the milestone weights in the reward, or delete them.

### RG503

**A weight differs across scoring files** · Proven · medium

The same check has different weights in different scoring files, so the reward depends on which file is read.

**Why it matters:** Which value applies depends on which file is read; the reward is ambiguous.

Bad:

```python
scoring_config.json: weight 0.2
task.json:           weight 0.25
```

Good:

```python
one weight, declared once, read everywhere
```

**Fix:** Make every scoring file declare the same weight.

### RG504

**A multiplicative gate can zero the whole reward** · Proven · medium

One failing check multiplies the whole reward to zero, so partial progress earns nothing.

**Why it matters:** One failing gate wipes out all partial credit, giving a sparse, cliff-shaped reward.

Bad:

```python
reward = base * (1 if lint_ok else 0)
```

Good:

```python
reward = base - 0.1 * (not lint_ok)    # unless the cliff is intended
```

**Fix:** Use an additive term or a soft gate unless the cliff is intended.

### RG505

**A participation term depends on feature success** · Proven · medium

A participation term only counts when the feature works, so it rewards success twice instead of rewarding effort.

**Why it matters:** Credit meant for attempting is only paid when the feature already works.

Bad:

```python
participation = 0.1 if feature_passes else 0
```

Good:

```python
participation = 0.1 if the agent submitted a non-empty change
```

**Fix:** Compute the participation term independently of feature success.

### RG506

**The reward-hacking heuristic points the wrong way** · Proven · high

The reward-hacking heuristic flags the wrong direction, so the hack it should catch passes unflagged.

**Why it matters:** The degenerate hack is not flagged, while honest runs may be.

Bad:

```python
suspect = train < 0.3 and eval > 0.8
```

Good:

```python
suspect = train > 0.8 and eval < 0.3   # high train, low held-out
```

**Fix:** Flag the direction the degenerate hack actually moves the metrics.

### RG507

**Scoring files contradict each other** · Proven · medium

Scoring files disagree on metadata (check classes, ids), so tools reading different files compute different rewards.

**Why it matters:** Contradictory metadata makes the reward definition ambiguous.

Bad:

```python
scoring_config.json: "test_x": "f2p"
task.json:           "test_x": "p2p"
```

Good:

```python
one source of truth, the other file generated from it
```

**Fix:** Make the metadata agree across every scoring file.

## Step 5: Cheating. Can a pass be faked?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG201](#rg201) | A pass can be faked with conftest.py | Proven | high |
| [RG202](#rg202) | Exiting early with code 0 fakes a pass | Proven | high |
| [RG203](#rg203) | A fake test report fakes a pass | Proven | high |
| [RG204](#rg204) | An object equal to everything fakes a pass | Proven | high |
| [RG205](#rg205) | Code planted outside the workspace fakes a pass | Proven | high |
| [RG206](#rg206) | A fake clock passes a timing test | Proven | high |
| [RG207](#rg207) | Changing which tests run fakes a pass | Proven | high |
| [RG208](#rg208) | The grading command can report success without passing tests | Needs your judgment | high |
| [RG210](#rg210) | The grader's report is written where the agent can write | Needs your judgment | high |
| [RG211](#rg211) | Test setup files are not restored before grading | Needs your judgment | high |
| [RG213](#rg213) | The grader reads test data the agent can change | Needs your judgment | high |
| [RG212](#rg212) | The agent can read the held-out tests | Proven | high |
| [RG214](#rg214) | The agent can rewrite the graded tests | Proven | critical |
| [RG215](#rg215) | The hack agent faked a pass through the grading setup | Proven | critical |

### RG201

**A pass can be faked with conftest.py** · Proven · high

A root conftest.py is planted the way an agent could. If it makes the unsolved task pass, the agent can learn to write it instead of the fix.

**Why it matters:** During RL the model can learn to forge the test report instead of fixing the code.

Bad:

```python
# conftest.py written by the agent
def pytest_runtest_makereport(item, call): ...force 'passed'...
```

Good:

```python
# verifier copies a clean tests/ into place and runs
pytest -p no:cacheprovider --rootdir=/tests /tests
```

**Fix:** Run tests from a clean copy of the test files in the verifier, or restore conftest.py before grading.

### RG202

**Exiting early with code 0 fakes a pass** · Proven · high

An early exit with code 0 (os._exit(0), sys.exit(0), process.exit(0)) is planted at the top of the files the fix changes, with no fix applied. If the grade passes, it trusts the exit code alone.

**Why it matters:** A grade decided by the exit code alone is passed by a program that exits 0 before any test runs.

Bad:

```python
pytest /tests; echo $? > /logs/verifier/reward.txt   # exit 0 = pass
```

Good:

```python
pytest /tests --junitxml=/out/r.xml
# reward from r.xml: every expected test present and passed
```

**Fix:** Decide the grade from a per-test report written by the test runner, and fail when it is missing or lists fewer tests than expected.

### RG203

**A fake test report fakes a pass** · Proven · high

A passing test report (JUnit XML, CTRF JSON, …) is written at the path the grader reads, with no fix applied. If the grade passes, the report is trusted as found.

**Why it matters:** If the grader reads a report the agent can write, the agent can write a passing one instead of fixing the code.

Bad:

```python
test -f results.xml || pytest --junitxml=results.xml
```

Good:

```python
rm -f /out/results.xml; pytest --junitxml=/out/results.xml   # outside the workspace
```

**Fix:** Delete any existing report before the tests run, write it where the agent can't, and fail when the run didn't produce it.

### RG204

**An object equal to everything fakes a pass** · Proven · high

Each function the fix changes is made to return an object whose __eq__ always returns True. If the tests still pass, they compare with == and nothing else.

**Why it matters:** Tests that compare results with == accept an object whose __eq__ always returns True, so the agent can return one instead of the answer.

Bad:

```python
assert total(cart) == 30
```

Good:

```python
r = total(cart)
assert type(r) is int and r == 30
```

**Fix:** Check the result's type before comparing (type(r) is int), or compare serialised values (json.dumps, repr).

### RG205

**Code planted outside the workspace fakes a pass** · Proven · high

Code is planted where Python loads it before any test: a .pth import hook and a sitecustomize.py in site-packages, a patched pytest package, and a sitecustomize.py in the workspace. Each one forces pytest to report a pass or the process to exit 0.

**Why it matters:** sitecustomize.py, .pth import hooks or a patched pytest run before any test and can force a pass from outside the code under test.

Bad:

```python
[verifier]
environment_mode = "shared"   # site-packages edits survive to grading
```

Good:

```python
[verifier]
environment_mode = "separate"
# test.sh: PYTHONNOUSERSITE=1 python -I -m pytest /tests
```

**Fix:** Grade in a separate verifier environment (Harbor: environment_mode = "separate"); run Python with -I and PYTHONNOUSERSITE=1; delete sitecustomize, usercustomize and unknown .pth files before grading.

### RG206

**A fake clock passes a timing test** · Proven · high

On a task whose tests time the code, the clock is patched (time.perf_counter and friends advance by a microsecond per call; GPU synchronisation becomes a no-op), with no fix applied. If the grade passes, the speed check can be faked.

**Why it matters:** Performance tests that read the clock inside the agent's process can be passed by patching the clock (or skipping synchronisation) instead of making the code faster.

Bad:

```python
t = time.perf_counter(); run(); assert time.perf_counter() - t < 0.1
```

Good:

```python
# verifier times `python -I bench.py` from its own process and checks the output
```

**Fix:** Measure time from outside the agent's code (a verifier-owned process started with python -I) and check the results as well as the speed.

### RG207

**Changing which tests run fakes a pass** · Proven · high

Test selection is changed the way an agent could: pytest.ini addopts = --collect-only, testpaths pointing at a trivial test, and conftest.py marking every test skip or xfail. If the grade passes, the agent decides which tests count.

**Why it matters:** Test configuration the agent controls (pytest.ini addopts and testpaths, skip or xfail marks in conftest.py) can stop the graded tests from running or failing.

Bad:

```python
# the agent's pytest.ini
[pytest]
addopts = --collect-only -q
```

Good:

```python
pytest -c /tests/pytest.ini --rootdir=/tests /tests   # config the agent can't edit
```

**Fix:** Run the tests with a config the agent can't edit (pytest -c /tests/pytest.ini --rootdir=/tests -p no:cacheprovider) and fail when fewer tests ran than expected.

### RG208

**The grading command can report success without passing tests** · Needs your judgment · high

The grading command and the scripts it runs are read for patterns that make success unconditional or hide failures: `|| true` after the test run, cache-based selection, collect-only flags, an unconditional reward of 1, or no test run at all.

**Why it matters:** A grader that ignores the test run's exit status, runs only part of the tests, or only checks that files exist rewards work that doesn't pass the tests.

Bad:

```python
pytest /tests || true
echo 1 > /logs/verifier/reward.txt
```

Good:

```python
pytest /tests && echo 1 > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt
```

**Fix:** Derive the reward from the test run's real result; remove `|| true`, cache-based selection (--lf, --sw) and collect-only flags from the grading command.

### RG210

**The grader's report is written where the agent can write** · Needs your judgment · high

The grader reads its results from a file. If that file is somewhere the agent can write, the agent can write the results itself.

**Why it matters:** If any scoring step trusts a report already on disk, the agent can forge one.

Bad:

```python
grader reads /app/results.xml   # /app is the agent's workspace
```

Good:

```python
grader writes and reads /logs/verifier/results.xml (outside the workspace)
```

**Fix:** Write the report to a path outside the agent's workspace, in the verifier only.

### RG211

**Test setup files are not restored before grading** · Needs your judgment · high

Files the test run depends on (pytest.ini, conftest.py, fixtures) live in the workspace and are not restored before grading, so the agent's edits to them count.

**Why it matters:** Files like conftest.py or pytest.ini load before any test; editing them can fake a pass.

Bad:

```python
pytest.ini in the workspace; the agent edits addopts = -k 'not hard'
```

Good:

```python
the verifier restores pytest.ini and conftest.py from its own copy first
```

**Fix:** Restore test-infrastructure files from a known-good copy before grading.

### RG213

**The grader reads test data the agent can change** · Needs your judgment · high

The grader reads a test file or expected output from a path the agent can edit (its workspace, or an artifact carried to the verifier).

**Why it matters:** If the expected outputs or test files the grader reads live where the agent can edit them, the agent can change the answer key instead of the code.

Bad:

```python
# tests/test.sh
diff out.txt /app/tests/expected_output.txt
```

Good:

```python
# tests/test.sh
diff out.txt /tests/expected_output.txt   # shipped with the verifier
```

**Fix:** Ship test inputs and expected outputs with the verifier (tests/), or hash-check them before grading.

### RG212

**The agent can read the held-out tests** · Proven · high

The held-out tests are readable from the agent's workspace, so the agent can read the answers instead of solving the task.

**Why it matters:** The model can read what will be checked and special-case it.

Bad:

```python
COPY tests/ /app/tests/      # baked into the agent image
```

Good:

```python
tests/ is uploaded only at grading time, or chmod 700 and owned by root
```

**Fix:** chmod 700 the graded directory, or copy tests in only at grading time.

### RG214

**The agent can rewrite the graded tests** · Proven · critical

Every graded test the agent can see is rewritten so its body is `pass`, with no fix applied. If the grade passes, the grader runs the agent's copy of the tests.

**Why it matters:** Graded tests the agent can edit can be emptied, and the grader then passes anything.

Bad:

```python
# grader runs the tests from /app/tests, which the agent can edit
```

Good:

```python
# tests are copied in from the verifier at grading time; /app/tests is ignored
```

**Fix:** Grade with a clean copy of the tests, copied in at grading time or restored from the verifier image.

### RG215

**The hack agent faked a pass through the grading setup** · Proven · critical

A hack run passed, but only with changes outside the solution's source: test files, test config, conftest.py, sitecustomize.py, a replaced binary. Proven by replaying its commands (pass) and grading its source changes alone (fail).

**Why it matters:** The grade passed only because of changes outside the solution's source (tests, test config, the environment), so the reward can be forged.

Bad:

```python
echo 'def pytest_collection_modifyitems(items): items.clear()' > conftest.py
# grader: reward 1.0 with no fix
```

Good:

```python
the verifier copies in the tests and their config at grading time and grades only the solution's source files
```

**Fix:** Grade in a fresh verifier environment that receives only the solution's source files, with tests and their config copied in at grading time; leave the task out until then.

## Step 6: Fairness. Are the task and tests clear and fair?

| Rule | Title | Evidence | Severity |
| --- | --- | --- | --- |
| [RG401](#rg401) | A test depends on wording the task never specifies | Proven | high |
| [RG402](#rg402) | A test uses an internal name the task never mentions | Needs your judgment | medium |
| [RG403](#rg403) | A test pins the reference's exact wording | Needs your judgment | medium |
| [RG404](#rg404) | A test pins an exact call count | Needs your judgment | medium |
| [RG405](#rg405) | A test checks something the task doesn't ask for | Needs your judgment | high |
| [RG406](#rg406) | A test asserts nothing meaningful | Needs your judgment | medium |
| [RG408](#rg408) | The grader rejects a correct solution the task allows | Proven | high |
| [RG407](#rg407) | A test rejects a correct solution written differently | Needs your judgment | high |
| [RG409](#rg409) | A rubric criterion is not atomic or observable | Needs your judgment | medium |
| [RG410](#rg410) | Rubric criteria repeat, or weights don't match consequence | Needs your judgment | low |
| [RG411](#rg411) | A requirement in the task has no rubric criterion | Needs your judgment | medium |
| [RG701](#rg701) | The task statement doesn't say what to change | Needs your judgment | high |
| [RG702](#rg702) | The task statement gives away the solution | Needs your judgment | high |
| [RG703](#rg703) | The task statement depends on material the solver can't see | Needs your judgment | high |
| [RG704](#rg704) | A test checks something the solver can't know | Needs your judgment | high |

### RG401

**A test depends on wording the task never specifies** · Proven · high

A test only passes with wording that appears in the reference fix but not in the task statement, so a correct solution with other wording fails.

**Why it matters:** A correct solution that words a message differently fails, so correct work is punished.

Bad:

```python
assert err == 'Invalid amount supplied'   # the instruction never says this
```

Good:

```python
assert resp.status_code == 400   # or state the exact message in the instruction
```

**Fix:** Assert the behaviour, not the incidental string; or state the string in the task.

### RG402

**A test uses an internal name the task never mentions** · Needs your judgment · medium

A test calls an internal name (a private helper, a class) the task never mentions, so a correct solution organised differently fails.

**Why it matters:** A correct solution with different internals fails the test.

Bad:

```python
from app.billing import _round_cents    # never mentioned in instruction.md
```

Good:

```python
test through the public API, or name the function in the instruction
```

**Fix:** Test through the public interface, or name the symbol in the instruction.

### RG403

**A test pins the reference's exact wording** · Needs your judgment · medium

A test compares against the reference's exact text, punctuation included.

**Why it matters:** A correct solution that phrases output differently fails.

Bad:

```python
assert out == 'Done! 3 items processed.'
```

Good:

```python
assert '3' in out and 'processed' in out.lower()
```

**Fix:** Assert the behaviour, not the reference's exact wording.

### RG404

**A test pins an exact call count** · Needs your judgment · medium

A test asserts how many times something was called — an implementation detail, not behaviour.

**Why it matters:** A correct solution with a different call shape fails.

Bad:

```python
assert mock_save.call_count == 2
```

Good:

```python
assert store.get(order_id).status == 'saved'
```

**Fix:** Assert observable behaviour or outputs, not the number of internal calls.

### RG405

**A test checks something the task doesn't ask for** · Needs your judgment · high

With --deep-review, an LLM compares each test with the statement. A test that requires behaviour the statement contradicts or never asks for is flagged.

**Why it matters:** A solution that does exactly what the statement says fails, so correct work is punished.

Bad:

```python
statement: return 404 for an unknown slug
test: assert resp.status_code == 400
```

Good:

```python
test: assert resp.status_code == 404
```

**Fix:** Change the test to match the statement, or state the requirement in the task.

### RG406

**A test asserts nothing meaningful** · Needs your judgment · medium

With --deep-review, an LLM reads the tests. A test that only checks the code imports, runs or returns something gives reward for almost any change.

**Why it matters:** A test that only checks the code runs or imports gives reward for any change.

Bad:

```python
def test_rsvp():
    assert client.post('/rsvp', json=body) is not None
```

Good:

```python
def test_rsvp():
    r = client.post('/rsvp', json=body)
    assert r.json()['events'][0]['status'] == 'accepted'
```

**Fix:** Assert the outputs, state or effects the change is meant to produce.

### RG408

**The grader rejects a correct solution the task allows** · Proven · high

With --deep-review on a full run, an agent that may read the tests writes a complete implementation that follows the statement but makes a different reasonable choice where the statement is silent. Proven when the verifier fails it on that point only, and the agent's check of the stated requirement passes on it and on the reference fix.

**Why it matters:** A solution that does what the statement asks fails, so correct work is punished.

Bad:

```python
statement: classify(n) returns 'big' when n > 10
test: assert 'classify called' in captured_stdout   # never asked for
```

Good:

```python
test: assert classify(11) == 'big' and classify(1) == 'small'
```

**Fix:** State the detail in the task, or broaden the test; otherwise leave the task out.

### RG407

**A test rejects a correct solution written differently** · Needs your judgment · high

With --deep-review, an LLM reads the tests with the reference fix in view. A test that pins a detail of the reference the statement leaves open (exact message, internal name, call order) rejects other correct solutions.

**Why it matters:** The test pins a detail of the reference the statement leaves open, so other correct solutions fail.

Bad:

```python
assert str(err) == 'RSVP list must not be empty (got 0 items)'
```

Good:

```python
assert resp.status_code == 422   # the statement only asks for a rejection
```

**Fix:** Assert the behaviour the statement asks for, or state the detail in the task.

### RG409

**A rubric criterion is not atomic or observable** · Needs your judgment · medium

Each rubric criterion should check one thing a grader can see. Flagged: a criterion joining several checks, or one that relies on vague words without anything measurable.

**Why it matters:** A criterion that bundles several checks, or uses words like "good" or "appropriate", is graded inconsistently, so the reward is noisy.

Bad:

```python
- The analysis is thorough and well written, and the charts are appropriate
```

Good:

```python
- The analysis states the year-over-year change in revenue
- Every chart has labelled axes
```

**Fix:** Split it into criteria that each check one thing a grader can see in the deliverable.

### RG410

**Rubric criteria repeat, or weights don't match consequence** · Needs your judgment · low

Flagged: two criteria that say the same thing, every criterion weighted the same while some guard against serious mistakes, or no penalty criteria at all.

**Why it matters:** Duplicate criteria count one thing twice, and flat weights make a cosmetic slip cost as much as a wrong answer.

Bad:

```python
- [1] Uses the correct tax rate
- [1] Has a title
- [1] Has a title page
```

Good:

```python
- [5] Uses the correct tax rate
- [1] Has a title
- [-5] States a wrong total
```

**Fix:** Merge duplicates; weight criteria by how much a mistake would matter, and add penalty criteria for serious errors.

### RG411

**A requirement in the task has no rubric criterion** · Needs your judgment · medium

Each requirement in the statement (a sentence with must/should/include/…) should share its key words with at least one criterion.

**Why it matters:** Work the task asks for that the rubric never checks earns nothing, so the model learns to skip it.

Bad:

```python
statement: ... must include a 12-month forecast ...
rubric: (nothing about a forecast)
```

Good:

```python
rubric: - The report includes a month-by-month forecast for the next 12 months
```

**Fix:** Add a criterion for each requirement the statement makes.

### RG701

**The task statement doesn't say what to change** · Needs your judgment · high

With --deep-review, an LLM reads the statement. It is flagged when a solver can't tell what to change or when the task is done.

**Why it matters:** A solver can't tell what is wanted or when it is done, so the reward rewards guessing.

Bad:

```python
Improve the RSVP flow.
```

Good:

```python
Change POST /api/weddings/rsvp/<slug> to take an events_rsvp list, one {event_id, attending} per event, and store each answer separately.
```

**Fix:** State the change to make and how to tell it is done.

### RG702

**The task statement gives away the solution** · Needs your judgment · high

With --deep-review, an LLM reads the statement next to the reference fix. A statement that contains the fix's code, or the exact lines to change, is flagged.

**Why it matters:** The solver copies the answer instead of working it out, so training teaches copying.

Bad:

```python
In views.py replace `guest.rsvp = body['rsvp']` with `for e in body['events_rsvp']: ...`
```

Good:

```python
Each guest's answer must be stored per event; existing answers must be kept.
```

**Fix:** Describe the behaviour wanted; take out the code and the exact lines to change.

### RG703

**The task statement depends on material the solver can't see** · Needs your judgment · high

With --deep-review, an LLM reads the statement. A statement that relies on something the solver can't see (an attachment, a screenshot, a linked issue or chat) is flagged.

**Why it matters:** The statement points at an attachment, image or discussion that isn't there, so part of the task is unknowable.

Bad:

```python
Make the page match the attached mockup (see #412 for the agreed behaviour).
```

Good:

```python
State in the task what the mockup and the issue decided.
```

**Fix:** Put what that material says into the statement itself.

### RG704

**A test checks something the solver can't know** · Needs your judgment · high

With --deep-review and the reference hidden, LLM judges read the statement, the pre-change repository and the tests. An assertion that expects a name, value or format nobody could know from those is flagged, with the assertion quoted.

**Why it matters:** A correct solution fails because the test expects a name, value or format that neither the statement nor the repository gives.

Bad:

```python
statement: report the dispatch result
test: assert body == {'already_dispatched': True}
```

Good:

```python
statement: respond with {"already_dispatched": true} when the order was placed before
```

**Fix:** State that detail in the task, or loosen the test so any reasonable choice passes.
