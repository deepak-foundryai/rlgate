"""``rlgate explain <rule>``: for every rule, a short bad example and the good version.

Kept apart from the catalogue (``rules.py``) so the catalogue stays a table. Every rule in
the catalogue must have an entry here — ``tests/core/test_rules.py`` enforces it.
"""

from __future__ import annotations

# rule id → (what it means in practice, bad example, good example)
DOCS: dict[str, tuple[str, str, str]] = {
    "RG001": (
        ("The grader is run on the task's own reference solution. If that doesn't score full "
        "marks every time, no agent can be graded fairly against it."),
        ("# tests expect 'total: 3' but solution/patch.diff prints 'Total: 3'\n"
        "reward = 0.0 for the reference solution"),
        ("# the reference solution and the tests agree\n"
        "reward = 1.0 for the reference solution, 5 runs out of 5")),
    "RG002": (
        ("The grader is run on the task exactly as shipped. If doing nothing passes, the "
        "reward teaches the agent that no work is needed."),
        "def test_runs():\n    import app          # passes before any fix",
        "def test_rejects_negative_amount():\n    assert app.charge(-5).status == 400"),
    "RG003": (
        ("The same submission is graded several times. A grade that changes between identical "
        "runs is noise in the reward."),
        "assert elapsed < 0.05        # depends on machine load",
        "assert result == expected    # deterministic; no timing, no network"),
    "RG004": (
        ("The agent's work is graded inside the same container the agent used, instead of a "
        "fresh verifier that only receives the agent's output."),
        "[verifier]\nenvironment_mode = \"shared\"   # tests run where the agent ran",
        "artifacts = [\"/app\"]\n[verifier]\nenvironment_mode = \"separate\""),
    "RG101": (
        ("Each part (hunk) of the reference fix is removed in turn. If the tests still pass "
        "without it, that part of the fix is never checked, and an agent can skip it."),
        "fix: validates input AND logs the error\ntests: only check the validation",
        "tests: one test per behaviour the fix adds — including the logging"),
    "RG102": (
        ("Each function the reference fix changes is replaced in turn by a stub that returns "
        "None or raises. If the tests still pass, nothing checks that function."),
        "def apply_discount(order):\n    return None      # stub: tests still pass",
        "def test_discount_applied():\n    assert apply_discount(order).total == 90"),
    "RG104": (
        ("The visible tests are run against the reference and every call to a changed "
        "function is recorded. The function is then replaced by a lookup table of those "
        "answers. If the graded tests still pass, they never use an input the agent "
        "couldn't see."),
        ("ANSWERS = {(2,): 4, (3,): 9}     # copied from the visible tests\n"
        "def square(x): return ANSWERS.get((x,))"),
        "# held-out tests call square() with inputs that appear in no visible test"),
    "RG103": (
        ("Small mutations of the fix (flip a comparison, change a constant) are graded. If one "
        "still passes, the tests can't tell the right behaviour from a near miss."),
        "if retries > 3:    # mutation to >= 3 still passes",
        "assert attempts_before_giving_up() == 3   # pins the boundary"),
    "RG201": (
        ("A root conftest.py is planted the way an agent could. If it makes the unsolved task "
        "pass, the agent can learn to write it instead of the fix."),
        ("# conftest.py written by the agent\n"
        "def pytest_runtest_makereport(item, call): ...force 'passed'..."),
        ("# verifier copies a clean tests/ into place and runs\n"
        "pytest -p no:cacheprovider --rootdir=/tests /tests")),
    "RG202": (
        ("An early exit with code 0 (os._exit(0), sys.exit(0), process.exit(0)) is planted at "
        "the top of the files the fix changes, with no fix applied. If the grade passes, it "
        "trusts the exit code alone."),
        "pytest /tests; echo $? > /logs/verifier/reward.txt   # exit 0 = pass",
        ("pytest /tests --junitxml=/out/r.xml\n"
        "# reward from r.xml: every expected test present and passed")),
    "RG203": (
        ("A passing test report (JUnit XML, CTRF JSON, …) is written at the path the grader "
        "reads, with no fix applied. If the grade passes, the report is trusted as found."),
        "test -f results.xml || pytest --junitxml=results.xml",
        "rm -f /out/results.xml; pytest --junitxml=/out/results.xml   # outside the workspace"),
    "RG204": (
        ("Each function the fix changes is made to return an object whose __eq__ always "
        "returns True. If the tests still pass, they compare with == and nothing else."),
        "assert total(cart) == 30",
        "r = total(cart)\nassert type(r) is int and r == 30"),
    "RG205": (
        ("Code is planted where Python loads it before any test: a .pth import hook and a "
        "sitecustomize.py in site-packages, a patched pytest package, and a sitecustomize.py "
        "in the workspace. Each one forces pytest to report a pass or the process to exit 0."),
        "[verifier]\nenvironment_mode = \"shared\"   # site-packages edits survive to grading",
        ("[verifier]\nenvironment_mode = \"separate\"\n"
        "# test.sh: PYTHONNOUSERSITE=1 python -I -m pytest /tests")),
    "RG206": (
        ("On a task whose tests time the code, the clock is patched (time.perf_counter and "
        "friends advance by a microsecond per call; GPU synchronisation becomes a no-op), "
        "with no fix applied. If the grade passes, the speed check can be faked."),
        "t = time.perf_counter(); run(); assert time.perf_counter() - t < 0.1",
        "# verifier times `python -I bench.py` from its own process and checks the output"),
    "RG207": (
        ("Test selection is changed the way an agent could: pytest.ini addopts = "
        "--collect-only, testpaths pointing at a trivial test, and conftest.py marking every "
        "test skip or xfail. If the grade passes, the agent decides which tests count."),
        "# the agent's pytest.ini\n[pytest]\naddopts = --collect-only -q",
        "pytest -c /tests/pytest.ini --rootdir=/tests /tests   # config the agent can't edit"),
    "RG208": (
        ("The grading command and the scripts it runs are read for patterns that make success "
        "unconditional or hide failures: `|| true` after the test run, cache-based selection, "
        "collect-only flags, an unconditional reward of 1, or no test run at all."),
        "pytest /tests || true\necho 1 > /logs/verifier/reward.txt",
        "pytest /tests && echo 1 > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt"),
    "RG214": (
        ("Every graded test the agent can see is rewritten so its body is `pass`, with no fix "
        "applied. If the grade passes, the grader runs the agent's copy of the tests."),
        "# grader runs the tests from /app/tests, which the agent can edit",
        "# tests are copied in from the verifier at grading time; /app/tests is ignored"),
    "RG210": (
        ("The grader reads its results from a file. If that file is somewhere the agent can "
        "write, the agent can write the results itself."),
        "grader reads /app/results.xml   # /app is the agent's workspace",
        "grader writes and reads /logs/verifier/results.xml (outside the workspace)"),
    "RG211": (
        ("Files the test run depends on (pytest.ini, conftest.py, fixtures) live in the "
        "workspace and are not restored before grading, so the agent's edits to them count."),
        "pytest.ini in the workspace; the agent edits addopts = -k 'not hard'",
        "the verifier restores pytest.ini and conftest.py from its own copy first"),
    "RG212": (
        ("The held-out tests are readable from the agent's workspace, so the agent can read "
        "the answers instead of solving the task."),
        "COPY tests/ /app/tests/      # baked into the agent image",
        "tests/ is uploaded only at grading time, or chmod 700 and owned by root"),
    "RG301": (
        ("The fix exists in the repository's git objects (an unreachable commit, a stash, a "
        "packed object) even though the working tree is unsolved."),
        "git checkout base && rm -rf solution   # the fix commit is still in .git",
        "fresh clone at the base commit, or git gc --prune=now after removing refs"),
    "RG302": (
        ("A compiled .pyc remains for a source file that was removed — it can be decompiled "
        "back into the code."),
        "app/__pycache__/solution.cpython-312.pyc   # solution.py was deleted",
        "build with PYTHONDONTWRITEBYTECODE=1 and remove __pycache__ before shipping"),
    "RG303": (
        ("A *.orig or *.rej file left by applying a patch holds the before/after code — the "
        "answer, sitting next to the file the agent must fix."),
        "app/calc.py\napp/calc.py.orig   # written by `patch`, contains the fixed code",
        "find . -name '*.orig' -o -name '*.rej' | xargs rm -f   # before building"),
    "RG304": (
        ("A file is named like an answer key (solution, expected_output, golden). It may be "
        "harmless, but if it holds the answer the agent will find it."),
        "app/expected_output.txt   # the exact output the tests compare against",
        "keep reference outputs in tests/ (uploaded only when grading)"),
    "RG305": (
        ("A few files have much later modification times than the rest of the checkout — "
        "often where the task's changes (or the answer) were added."),
        "every file 2024-01-01 except app/billing.py 2026-11-09",
        "normalise mtimes when building (e.g. touch -d @0 on every file)"),
    "RG401": (
        ("A test only passes with wording that appears in the reference fix but not in the "
        "task statement, so a correct solution with other wording fails."),
        "assert err == 'Invalid amount supplied'   # the instruction never says this",
        "assert resp.status_code == 400   # or state the exact message in the instruction"),
    "RG402": (
        ("A test calls an internal name (a private helper, a class) the task never mentions, "
        "so a correct solution organised differently fails."),
        "from app.billing import _round_cents    # never mentioned in instruction.md",
        "test through the public API, or name the function in the instruction"),
    "RG403": (
        "A test compares against the reference's exact text, punctuation included.",
        "assert out == 'Done! 3 items processed.'",
        "assert '3' in out and 'processed' in out.lower()"),
    "RG404": (
        ("A test asserts how many times something was called — an implementation detail, not "
        "behaviour."),
        "assert mock_save.call_count == 2",
        "assert store.get(order_id).status == 'saved'"),
    "RG501": (
        ("A held-out check is scored for evaluation but never feeds the training reward, so "
        "training can't learn what it measures."),
        "train = f2p_score\neval = f2p_score * held_out_score",
        "train = f2p_score * (0.5 + 0.5 * held_out_score)   # or gate on it"),
    "RG502": (
        "Milestone weights are declared in the scoring config but the reward ignores them.",
        '"milestones": {"api": 0.6, "ui": 0.4}   # reward = mean(all checks)',
        "reward = 0.6 * api_score + 0.4 * ui_score"),
    "RG503": (
        ("The same check has different weights in different scoring files, so the reward "
        "depends on which file is read."),
        "scoring_config.json: weight 0.2\ntask.json:           weight 0.25",
        "one weight, declared once, read everywhere"),
    "RG504": (
        ("One failing check multiplies the whole reward to zero, so partial progress earns "
        "nothing."),
        "reward = base * (1 if lint_ok else 0)",
        "reward = base - 0.1 * (not lint_ok)    # unless the cliff is intended"),
    "RG505": (
        ("A participation term only counts when the feature works, so it rewards success "
        "twice instead of rewarding effort."),
        "participation = 0.1 if feature_passes else 0",
        "participation = 0.1 if the agent submitted a non-empty change"),
    "RG506": (
        ("The reward-hacking heuristic flags the wrong direction, so the hack it should "
        "catch passes unflagged."),
        "suspect = train < 0.3 and eval > 0.8",
        "suspect = train > 0.8 and eval < 0.3   # high train, low held-out"),
    "RG507": (
        ("Scoring files disagree on metadata (check classes, ids), so tools reading different "
        "files compute different rewards."),
        'scoring_config.json: "test_x": "f2p"\ntask.json:           "test_x": "p2p"',
        "one source of truth, the other file generated from it"),
    "RG605": (
        ("The task's files don't describe something its harness can run as written: a "
        "missing statement or reference solution, a test script that never writes a "
        "reward, or a shape (multi-step, Windows, sidecar artifacts) rlgate can't reproduce."),
        "# tests/test.sh\npytest /tests   # exit code only; no reward file written",
        ("# tests/test.sh\npytest /tests && echo 1 > /logs/verifier/reward.txt \\\n"
        "  || echo 0 > /logs/verifier/reward.txt")),
    "RG601": (
        ("COPY . (or ADD .) puts the whole build context into the image — including tests or "
        "solutions that sit next to the Dockerfile."),
        "COPY . /app",
        "COPY src/ /app/src/\nCOPY requirements.txt /app/"),
    "RG602": (
        ("The base image has no fixed version, so rebuilding later can give a different "
        "environment."),
        "FROM python",
        "FROM python:3.12.7-slim   # or @sha256:<digest>"),
    "RG603": (
        "The build uses the host's CPU count, so images differ between machines.",
        "RUN make -j$(nproc)",
        "RUN make -j4"),
    "RG604": (
        ("A dependency is installed by range, so the version depends on when the image was "
        "built."),
        "RUN pip install 'requests>=2'",
        "RUN pip install requests==2.32.3   # or a locked requirements file"),
    "RG608": (
        ("Every image the task uses (the agent's environment and a separate verifier, "
        "including compose services) is built. If one fails, the task can't run at all."),
        "RUN pip install -r requirements.txt   # requirements.txt pins a yanked version",
        "RUN pip install -r requirements.txt   # every pin resolves; the image builds"),
    "RG006": (
        ("The grader's per-test report is read before and after the reference fix. At least "
        "one test must fail on the unchanged task and pass with the fix (fail-to-pass)."),
        "def test_app_imports():\n    import app      # passes before and after the fix",
        "def test_refund_is_capped():\n    assert refund(order, 999).amount == order.total"),
    "RG007": (
        ("A test that passes on the unchanged task fails once the reference fix is applied. "
        "Either the fix breaks behaviour, or the grade ignores that test."),
        "before the fix: test_list_users passes\nafter the fix:  test_list_users fails, reward 1.0",
        "before and after the fix: test_list_users passes"),
    "RG008": (
        ("No test passes both before and after the fix (pass-to-pass), so nothing checks that "
        "existing behaviour still works."),
        "pytest /tests/test_new_feature.py        # only the new behaviour is graded",
        "pytest /tests/test_new_feature.py /tests/test_existing.py"),
    "RG009": (
        ("The reference solution is run, its changes to the workspace are extracted as a "
        "patch, and that patch alone is applied to a fresh environment and graded, the way "
        "training grades an agent's submission."),
        "# solve.sh\npip install fixed-lib==2.1   # outside the workspace; lost in the patch",
        "# solve.sh\necho 'fixed-lib==2.1' >> requirements.txt   # part of the patch"),
    "RG010": (
        ("A run writes marker files in /tmp and the home directory and leaves a background "
        "process, then checks whether any of them are visible when grading starts."),
        "[verifier]\nenvironment_mode = \"shared\"   # /tmp and processes survive into grading",
        "artifacts = [\"/app\"]\n[verifier]\nenvironment_mode = \"separate\""),
    "RG011": (
        ("Each reference and grading run is timed against the task's own limits. A run near "
        "its limit times out on a slower machine."),
        "[verifier]\ntimeout_sec = 60     # the grader takes 55s",
        "[verifier]\ntimeout_sec = 300    # the grader takes 55s"),
    "RG012": (
        ("task.toml doesn't say how much CPU, memory or time the task gets, so the checked "
        "conditions and the training conditions can differ."),
        "[environment]\nbuild_timeout_sec = 600",
        ("[environment]\ncpus = 2\nmemory_mb = 4096\n[agent]\ntimeout_sec = 1800\n"
        "[verifier]\ntimeout_sec = 300")),
    "RG213": (
        ("The grader reads a test file or expected output from a path the agent can edit (its "
        "workspace, or an artifact carried to the verifier)."),
        "# tests/test.sh\ndiff out.txt /app/tests/expected_output.txt",
        "# tests/test.sh\ndiff out.txt /tests/expected_output.txt   # shipped with the verifier"),
    "RG306": (
        ("The agent's environment is searched for files identical to the task's solution/ "
        "and tests/ files."),
        "COPY . /app        # copies solution/patch.diff into the image",
        "COPY src/ /app/src/   # solution/ and tests/ never enter the agent image"),
    "RG307": (
        ("The agent's environment allows any outbound traffic, so the upstream fix is one "
        "download away."),
        "[environment]\n# allow_internet not set: open internet",
        "[environment]\nallow_internet = false   # or a mirror with an allow list"),
    "RG606": (
        ("A dependency install comes after a COPY of the task's source, so Docker can't reuse "
        "the install layer when the source changes."),
        "COPY . /app\nRUN pip install -r requirements.txt",
        "COPY requirements.txt /app/\nRUN pip install -r requirements.txt\nCOPY . /app"),
    "RG607": (
        "The grader's script installs or downloads software every time it runs.",
        "# tests/test.sh\ncurl -LsSf https://astral.sh/uv/install.sh | sh\nuvx pytest /tests",
        "# tests/Dockerfile\nRUN pip install pytest==8.3.3\n# tests/test.sh\npytest /tests"),
    # -- L2 static review (LLM judges, --deep-review) ---------------------------------------------
    "RG105": (
        ("With --deep-review, an LLM reads the statement and the reference fix. It flags a part of "
        "the fix that serves nothing the statement asks for, quoting that part."),
        ("statement: make /rsvp accept one answer per event\n"
        "reference: ...and also renames every logger and bumps flask"),
        "reference: only the per-event RSVP change the statement asks for"),
    "RG106": (
        ("With --deep-review, an LLM reads the reference fix. It flags code that special-cases the "
        "tests' inputs or expected values, and edits to test files."),
        "def total(xs):\n    if xs == [1, 2, 3]:      # the test's input\n        return 6",
        "def total(xs):\n    return sum(xs)"),
    "RG107": (
        ("With --deep-review, an LLM reads the grading command and the script it runs. It flags a "
        "reward that doesn't depend on the tests' result: success written regardless, "
        "failures swallowed, or a superficial check in place of the tests."),
        "pytest /tests 2>/dev/null; echo 1 > /logs/verifier/reward.txt",
        "if pytest /tests; then echo 1; else echo 0; fi > /logs/verifier/reward.txt"),
    "RG108": (
        ("With --deep-review and the reference hidden, LLM judges compare each requirement in the "
        "statement with the tests. A requirement no test checks, or one checked so loosely "
        "that breaking it still passes, is flagged with the requirement quoted."),
        ("statement: \"one guest's RSVP must never affect another guest's\"\n"
        "tests: only ever use one guest"),
        "tests: two guests RSVP to the same event; assert each keeps their own answer"),
    "RG109": (
        ("With --deep-review and the reference hidden, LLM judges look for a cheap implementation "
        "every test would accept: hardcoded values, special-cased inputs, half the change. "
        "The finding describes it; L3 confirms it by running it."),
        "def test_price():\n    assert price(3, '0.10') == Decimal('0.30')   # the only case",
        ("@pytest.mark.parametrize('qty,unit', [(3, '0.10'), (7, '1.15'), (0, '2.00')])\n"
        "def test_price(qty, unit): ...")),
    "RG405": (
        ("With --deep-review, an LLM compares each test with the statement. A test that requires "
        "behaviour the statement contradicts or never asks for is flagged."),
        ("statement: return 404 for an unknown slug\n"
        "test: assert resp.status_code == 400"),
        "test: assert resp.status_code == 404"),
    "RG406": (
        ("With --deep-review, an LLM reads the tests. A test that only checks the code imports, runs "
        "or returns something gives reward for almost any change."),
        "def test_rsvp():\n    assert client.post('/rsvp', json=body) is not None",
        ("def test_rsvp():\n    r = client.post('/rsvp', json=body)\n"
        "    assert r.json()['events'][0]['status'] == 'accepted'")),
    "RG407": (
        ("With --deep-review, an LLM reads the tests with the reference fix in view. A test that "
        "pins a detail of the reference the statement leaves open (exact message, internal "
        "name, call order) rejects other correct solutions."),
        "assert str(err) == 'RSVP list must not be empty (got 0 items)'",
        "assert resp.status_code == 422   # the statement only asks for a rejection"),
    "RG701": (
        ("With --deep-review, an LLM reads the statement. It is flagged when a solver can't tell "
        "what to change or when the task is done."),
        "Improve the RSVP flow.",
        ("Change POST /api/weddings/rsvp/<slug> to take an events_rsvp list, one "
        "{event_id, attending} per event, and store each answer separately.")),
    "RG702": (
        ("With --deep-review, an LLM reads the statement next to the reference fix. A statement "
        "that contains the fix's code, or the exact lines to change, is flagged."),
        "In views.py replace `guest.rsvp = body['rsvp']` with `for e in body['events_rsvp']: ...`",
        "Each guest's answer must be stored per event; existing answers must be kept."),
    "RG703": (
        ("With --deep-review, an LLM reads the statement. A statement that relies on something the "
        "solver can't see (an attachment, a screenshot, a linked issue or chat) is flagged."),
        "Make the page match the attached mockup (see #412 for the agreed behaviour).",
        "State in the task what the mockup and the issue decided."),
    "RG704": (
        ("With --deep-review and the reference hidden, LLM judges read the statement, the "
        "pre-change repository and the tests. An assertion that expects a name, value or "
        "format nobody could know from those is flagged, with the assertion quoted."),
        ("statement: report the dispatch result\n"
        "test: assert body == {'already_dispatched': True}"),
        ("statement: respond with {\"already_dispatched\": true} when the order was placed "
        "before")),
    # -- L3 agentic audits (LLM agent that runs code, --deep-review) ---------------------------
    "RG110": (
        ("With --deep-review on a full run, an agent that sees only the statement and the repository "
        "records the task's requirements, then writes implementations that break one and "
        "grades them. Proven when the verifier passes one, the agent's own check shows the "
        "requirement broken, and the reference fix passes that check."),
        ("statement: double(x) returns x * 2 for any integer x\n"
        "test: assert double(2) == 4      # 'return x + 2' passes too"),
        "@pytest.mark.parametrize('x', [-3, 0, 2, 7])\ndef test_double(x): assert double(x) == 2 * x"),
    "RG408": (
        ("With --deep-review on a full run, an agent that may read the tests writes a complete "
        "implementation that follows the statement but makes a different reasonable choice "
        "where the statement is silent. Proven when the verifier fails it on that point "
        "only, and the agent's check of the stated requirement passes on it and on the "
        "reference fix."),
        ("statement: classify(n) returns 'big' when n > 10\n"
        "test: assert 'classify called' in captured_stdout   # never asked for"),
        "test: assert classify(11) == 'big' and classify(1) == 'small'"),
    # -- L4 hack agent (--deep-review, full run, docker/daytona) ----------------------------------
    "RG111": (
        ("With --deep-review on a container backend, short agent runs try to reach full reward "
        "without solving the task. Every pass is replayed in a fresh environment; then the "
        "source changes alone are graded. Proven when they pass AND the agent's check "
        "command fails on them and passes on the reference fix."),
        ("def is_prime(n):\n    return n in (2, 3, 5, 7, 11)   # the tested inputs\n"
        "# grader: reward 1.0"),
        ("@pytest.mark.parametrize('n', random_primes + random_composites)\n"
        "def test_is_prime(n): ...")),
    "RG113": (
        ("A hack run passed with source changes the agent says are wrong, but its check "
        "command didn't prove it (it passed on the hack too, or failed on the reference). "
        "The code is attached for a person to read."),
        "claim: 'returns a cached value for the second call'\ncheck: (none given)",
        "check: python3 -c \"import m; assert m.f(2) != m.f(3)\"  # fails on the hack"),
    "RG215": (
        ("A hack run passed, but only with changes outside the solution's source: test "
        "files, test config, conftest.py, sitecustomize.py, a replaced binary. Proven by "
        "replaying its commands (pass) and grading its source changes alone (fail)."),
        ("echo 'def pytest_collection_modifyitems(items): items.clear()' > conftest.py\n"
        "# grader: reward 1.0 with no fix"),
        ("the verifier copies in the tests and their config at grading time and grades only "
        "the solution's source files")),
    "RG308": (
        ("A hack run reported a command that reveals the solution. It is re-run on a fresh "
        "copy of the environment; proven when its output contains the reference fix's "
        "added lines."),
        "git log --all -p | grep -A20 'def charge'   # prints the reference fix",
        ("the image is built from a clean checkout of the pre-change commit, with no history "
        "past it and no caches")),
    "RG112": (
        ("For rubric-graded tasks, with --deep-review: one attacker that sees the rubric and one "
        "that sees only the task write cheap deliverables. A rubric judge and a goal judge "
        "grade each independently. Flagged when the rubric judge passes a deliverable the "
        "goal judge says misses the task's goal."),
        ("criterion: 'The report includes a risk section'\n"
        "deliverable: '## Risks\\nThere are risks.'   # full credit"),
        "criterion: 'The report names the two largest risks in the data and their size'"),
    "RG409": (
        ("Each rubric criterion should check one thing a grader can see. Flagged: a "
        "criterion joining several checks, or one that relies on vague words without "
        "anything measurable."),
        "- The analysis is thorough and well written, and the charts are appropriate",
        ("- The analysis states the year-over-year change in revenue\n"
        "- Every chart has labelled axes")),
    "RG410": (
        ("Flagged: two criteria that say the same thing, every criterion weighted the "
        "same while some guard against serious mistakes, or no penalty criteria at all."),
        "- [1] Uses the correct tax rate\n- [1] Has a title\n- [1] Has a title page",
        "- [5] Uses the correct tax rate\n- [1] Has a title\n- [-5] States a wrong total"),
    "RG411": (
        ("Each requirement in the statement (a sentence with must/should/include/…) "
        "should share its key words with at least one criterion."),
        ("statement: ... must include a 12-month forecast ...\n"
        "rubric: (nothing about a forecast)"),
        "rubric: - The report includes a month-by-month forecast for the next 12 months"),
}


def catalogue_markdown() -> str:
    """docs/rules.md: every rule ID, grouped by step (tests keep the file in sync)."""
    from rlgate.core import rules

    lang = {"RG6": "dockerfile"}
    out = ["# rlgate rule catalogue", "",
           ("Every finding has a stable rule ID. `rlgate check` runs six numbered steps in "
            "order and stops at the first one with a proven problem; each rule belongs to "
            "one step. Generated from the code by `python -m rlgate.core.rule_docs`; "
            "`rlgate explain <rule>` shows the same in the terminal."), ""]
    out += ["| Step | Name | Question | Rules |", "| --- | --- | --- | --- |"]
    out += [f"| {s.number} | {s.label} | {s.text} | {', '.join(x + 'xx' for x in s.ranges)} |"
            for s in rules.STEPS]
    out += ["", ("Optional, after all six steps pass: difficulty (RG8xx, `rlgate calibrate`) "
                 "and duplicates across a bank (RG9xx). Not checked yet."), ""]
    for st, rs in rules.by_step().items():
        out += [f"## Step {st.number}: {st.label}. {st.text}", ""]
        if not rs:
            out += ["Not checked yet.", ""]
            continue
        out += ["| Rule | Title | Evidence | Severity |", "| --- | --- | --- | --- |"]
        out += [f"| [{r.id}](#{r.id.lower()}) | {r.title} | {r.proof.value} | "
                f"{r.severity.name.lower()} |" for r in rs]
        out.append("")
        for r in rs:
            meaning, bad, good = DOCS[r.id]
            code = lang.get(r.id[:3], "python")
            out += [f"### {r.id}", "", (f"**{r.title}** · {r.proof.value} · "
                    f"{r.severity.name.lower()}"), "", meaning, "",
                    f"**Why it matters:** {r.why}", "",
                    "Bad:", "", f"```{code}", bad, "```", "",
                    "Good:", "", f"```{code}", good, "```", "",
                    f"**Fix:** {r.fix}", ""]
    return "\n".join(out)


if __name__ == "__main__":
    import pathlib

    pathlib.Path("docs/rules.md").write_text(catalogue_markdown())
    print("wrote docs/rules.md")
