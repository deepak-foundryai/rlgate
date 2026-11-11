"""The rule catalogue: the stable, user-facing identity of every check.

A *probe* is an implementation (``grader_strength.revert_probe``); a *rule* is what the user
sees (``RG101 · A fix hunk is not tested``). One probe can emit several rules (cache_scan →
RG302/RG303/RG304). Every finding carries a ``rule_id`` from this catalogue; the four-part
finding (What / Why / Proof / Fix) is assembled from the finding (What, Proof, specific Fix)
and the rule (Why, default Fix).

Rule IDs are stable forever: never renumber or reuse one. Ranges follow the ten user-facing
questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rlgate.core.finding import Severity


class Question(Enum):
    """The ten questions the user sees, in display order. Value = rule-ID prefix."""

    TASK_WORKS = "RG0"
    GRADER_CHECKS_FIX = "RG1"
    PASS_CAN_BE_FAKED = "RG2"
    ANSWER_LEAKED = "RG3"
    TESTS_FAIR = "RG4"
    REWARD_WIRED = "RG5"
    BUILD_REPRODUCIBLE = "RG6"
    TASK_CLEAR = "RG7"
    DIFFICULTY_RIGHT = "RG8"
    DUPLICATE = "RG9"

    @property
    def text(self) -> str:
        return _QUESTION_TEXT[self]


_QUESTION_TEXT = {
    Question.TASK_WORKS: "Does the task work?",
    Question.GRADER_CHECKS_FIX: "Does the grader check the fix?",
    Question.PASS_CAN_BE_FAKED: "Can a pass be faked?",
    Question.ANSWER_LEAKED: "Is the answer leaked?",
    Question.TESTS_FAIR: "Are the tests fair?",
    Question.REWARD_WIRED: "Is the reward wired correctly?",
    Question.BUILD_REPRODUCIBLE: "Is the build reproducible?",
    Question.TASK_CLEAR: "Is the task clear?",
    Question.DIFFICULTY_RIGHT: "Is the difficulty right?",
    Question.DUPLICATE: "Is it a duplicate or contaminated?",
}


class Step(Enum):
    """The six numbered steps ``rlgate check`` runs, in order. A broken build or baseline
    (steps 1-2) stops the run; ``-x`` stops at any failed step. Value = number."""

    BUILD = 1
    BASELINE = 2
    LEAKS = 3
    GRADER = 4
    CHEATING = 5
    FAIRNESS = 6

    @property
    def number(self) -> int:
        return self.value

    @property
    def label(self) -> str:
        return _STEP_INFO[self][0]

    @property
    def text(self) -> str:
        """The question the step answers."""
        return _STEP_INFO[self][1]

    @property
    def slug(self) -> str:
        return self.label.lower()

    @property
    def ranges(self) -> tuple[str, ...]:
        return _STEP_INFO[self][2]


_STEP_INFO = {
    Step.BUILD: ("Build", "Can the task be built and run?", ("RG6",)),
    Step.BASELINE: ("Baseline", "Does the task work?", ("RG0",)),
    Step.LEAKS: ("Leaks", "Is the answer hidden from the agent?", ("RG3",)),
    Step.GRADER: ("Grader", "Does the grader really check the fix?", ("RG1", "RG5")),
    Step.CHEATING: ("Cheating", "Can a pass be faked?", ("RG2",)),
    Step.FAIRNESS: ("Fairness", "Are the task and tests clear and fair?", ("RG4", "RG7")),
}
STEPS: tuple[Step, ...] = tuple(Step)
_STEP_OF_RANGE = {r: s for s in Step for r in s.ranges}


def step_of(rule_id: str) -> Step | None:
    """The step a rule belongs to; None for the optional checks (RG8xx, RG9xx)."""
    return _STEP_OF_RANGE.get(rule_id[:3])


def step_of_probe(probe_id: str) -> Step | None:
    rs = for_probe(probe_id)
    return step_of(rs[0].id) if rs else None


class Proof(Enum):
    """What kind of finding a rule produces (plain labels from the checklist)."""

    PROVEN = "Proven"                       # execution-reproduced or a concrete file fact
    JUDGMENT = "Needs your judgment"        # deterministic signal; a person decides


@dataclass(frozen=True)
class Rule:
    id: str                 # "RG101" — stable forever
    probe_id: str           # the probe that emits it
    title: str              # plain-language, user-facing
    severity: Severity
    proof: Proof
    why: str                # why it matters for RL training (the "Why" line)
    fix: str                # default concrete action (a finding's fix_hint overrides it)

    @property
    def question(self) -> Question:
        return Question(self.id[:3])

    @property
    def step(self) -> Step | None:
        return step_of(self.id)


_P, _J = Proof.PROVEN, Proof.JUDGMENT
_H, _M, _L, _C = Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.CRITICAL

_RULES: list[Rule] = [
    # -- RG0xx Does the task work? (L0 baseline gate) --------------------------------------
    Rule("RG001", "baseline.invariant", "The reference solution does not pass", _C, _P,
         "If the gold fix can't earn full reward, every reward signal on this task is noise.",
         "Fix the reference solution or the grader until gold scores 1.0 on every run."),
    Rule("RG002", "baseline.invariant", "Doing nothing passes", _C, _P,
         "The model is rewarded without changing anything, so it learns to do nothing.",
         "Make the grader fail on the unchanged workspace (add a test the fix makes pass)."),
    Rule("RG003", "baseline.invariant", "The grade is flaky", _C, _P,
         "The same submission gets different rewards, so the reward is partly random.",
         "Remove nondeterminism (timing, ordering, network, randomness) from the flaky tests."),

    Rule("RG004", "hygiene.verifier_isolation", "The grader runs in the agent's environment",
         _M, _J,
         "Anything the agent leaves behind (processes, files outside the workspace, patched "
         "tools) is still there when its work is graded, so it can sway the grade.",
         "Grade in a separate verifier environment that receives only the agent's declared "
         "output (Harbor: [verifier] environment_mode = \"separate\" with artifacts)."),

    Rule("RG006", "baseline.invariant", "No test goes from failing to passing with the fix",
         _H, _P,
         "Without a fail-to-pass test, nothing in the grader shows the fix is what earns the "
         "reward.",
         "Add a test that fails on the unchanged task and passes with the reference fix."),
    Rule("RG007", "baseline.invariant", "The reference fix breaks a test that passed before",
         _H, _P,
         "A test that the fix breaks is either wrong or ignored by the grade; either way, "
         "regressions aren't rejected.",
         "Fix the test or the reference solution so every test that passed before still "
         "passes."),
    Rule("RG008", "baseline.invariant", "No existing tests guard against regressions", _L, _J,
         "With no pass-to-pass tests, a solution that breaks unrelated behaviour still earns "
         "full reward.",
         "Grade the existing tests too, so they must keep passing after the change."),
    Rule("RG009", "baseline.invariant", "The fix doesn't survive the trip to the grader", _C, _P,
         "Training extracts the agent's changes as a patch and grades that. If the reference "
         "only passes when run in place, the reward training sees differs from the one "
         "checked.",
         "Make the reference solution change only files inside the workspace (or the declared "
         "artifacts), so its patch alone passes."),
    Rule("RG010", "hygiene.carryover", "Changes outside the submission reach the grader", _H,
         _P,
         "Files, packages or processes the agent leaves behind outside its submission are still "
         "there when it is graded, so they can sway the grade.",
         "Grade in a fresh verifier environment that receives only the declared artifacts "
         "(Harbor: [verifier] environment_mode = \"separate\")."),
    Rule("RG011", "baseline.invariant", "The task runs too close to its time limit", _H, _P,
         "A run that hits its timeout scores 0 for reasons that have nothing to do with the "
         "solution, so the reward is noise.",
         "Speed up the grader or the solution, or raise timeout_sec in task.toml."),
    Rule("RG012", "hygiene.limits", "The task doesn't declare its resource limits", _L, _J,
         "Without declared CPU, memory and time limits, training runs it under whatever "
         "defaults apply, which may differ from what was checked.",
         "Declare [environment] cpus / memory_mb and [agent] / [verifier] timeout_sec in "
         "task.toml."),

    # -- RG1xx Does the grader check the fix? (L1 coverage) --------------------------------
    Rule("RG101", "grader_strength.revert_probe", "A part of the fix is not tested", _H, _P,
         "A solution that skips this part of the fix still earns full reward.",
         "Add a test that fails when this hunk of the fix is missing."),
    Rule("RG102", "grader_strength.stub_probe", "A changed function can be replaced by a stub",
         _H, _P,
         "A solution that doesn't implement this function at all still earns full reward.",
         "Add a test that calls this function and checks what it returns or does."),
    Rule("RG103", "grader_strength.mutation_probe", "A small change to the fix goes unnoticed",
         _M, _J,
         "A subtly wrong solution may earn full reward; it may also be a harmless "
         "(equivalent) change.",
         "Add an assertion that tells these behaviours apart, or waive it as equivalent."),
    Rule("RG104", "grader_strength.hardcode_probe", "Hardcoded answers to the visible tests "
         "pass", _H, _P,
         "The agent can read the visible tests and return their expected outputs instead of "
         "solving the task.",
         "Grade with held-out tests whose inputs the agent never sees, covering more inputs "
         "than the visible tests use."),

    # -- RG1xx, L2 static review (LLM, --deep-review): needs your judgment until L3 runs it -------
    Rule("RG105", "review.reference", "Part of the reference fix is unrelated to the task",
         _M, _J,
         "The checks that compare against the reference (revert, stub, mutation) and the "
         "difficulty estimate count work the task never asked for.",
         "Remove the unrelated change from the reference fix, or ask for it in the "
         "statement."),
    Rule("RG106", "review.reference", "The reference fix hardcodes answers or edits the tests",
         _C, _J,
         "A reference that special-cases the tests or changes them proves nothing about the "
         "task, and teaches that gaming the tests is the solution.",
         "Rewrite the reference to solve the task in general and leave the test files alone; "
         "keep the task out of training until then."),
    Rule("RG107", "review.verifier", "The grading command can pass without the tests passing",
         _C, _J,
         "A verifier that manufactures success, swallows failures or checks something "
         "superficial rewards work that doesn't solve the task.",
         "Make the reward depend on the tests' own result; keep the task out of training "
         "until then."),
    Rule("RG108", "review.enforcement", "A requirement in the task is not enforced by any test",
         _H, _J,
         "A solution can skip this requirement and still earn full reward.",
         "Add a test that fails when this requirement is not met."),
    Rule("RG109", "review.weak_pass", "A trivial or incomplete solution could pass", _H, _J,
         "The cheapest way to the reward is a shortcut, so that is what training teaches.",
         "Add tests the shortcut fails: more inputs, held-out values, the part it skips."),

    # -- RG1xx, L3 agentic audits (LLM agent that runs code, --deep-review): proven ---------------
    Rule("RG110", "agentic.strength", "An incomplete or wrong implementation passes", _H, _P,
         "An agent can skip or break a stated requirement and still earn full reward, so "
         "training teaches it to.",
         "Strengthen the tests so breaking this requirement fails them; otherwise leave the "
         "task out."),

    # -- RG1xx, L4 hack agent (--deep-review, full run, container backends) -----------------------
    Rule("RG111", "agentic.hack", "The hack agent passed with wrong code", _C, _P,
         "An agent told to cheat found wrong code that earns full reward, so training will "
         "find it too.",
         "Add tests that the hack's code fails (its check command shows what to test), then "
         "re-run the task through every step; leave the task out until then."),
    Rule("RG113", "agentic.hack", "The hack agent passed with code it says is wrong", _H, _J,
         "The grader passed code an agent wrote to be wrong; the agent's own check didn't "
         "prove it, so a person should read the code.",
         "Read the attached code: if it is wrong, add tests it fails; if it is a correct "
         "solution, nothing to do."),
    Rule("RG112", "agentic.rubric", "A deliverable that misses the goal earns rubric credit",
         _H, _J,
         "A rubric that credits work which doesn't achieve the task's goal teaches the model "
         "to satisfy the rubric's wording instead of doing the work.",
         "Add criteria (or penalty criteria) that the attack deliverable fails, and make "
         "every criterion check the goal, not the form."),

    # -- RG2xx Can a pass be faked? (L1 verdict channel) ------------------------------------
    Rule("RG201", "forgery.conftest_injection", "A pass can be faked with conftest.py", _H, _P,
         "During RL the model can learn to forge the test report instead of fixing the code.",
         "Run tests from a clean copy of the test files in the verifier, or restore "
         "conftest.py before grading."),
    Rule("RG202", "forgery.exit_code", "Exiting early with code 0 fakes a pass", _H, _P,
         "A grade decided by the exit code alone is passed by a program that exits 0 before "
         "any test runs.",
         "Decide the grade from a per-test report written by the test runner, and fail when "
         "it is missing or lists fewer tests than expected."),
    Rule("RG203", "forgery.report_forgery", "A fake test report fakes a pass", _H, _P,
         "If the grader reads a report the agent can write, the agent can write a passing "
         "one instead of fixing the code.",
         "Delete any existing report before the tests run, write it where the agent can't, "
         "and fail when the run didn't produce it."),
    Rule("RG204", "forgery.equality_override", "An object equal to everything fakes a pass",
         _H, _P,
         "Tests that compare results with == accept an object whose __eq__ always returns "
         "True, so the agent can return one instead of the answer.",
         "Check the result's type before comparing (type(r) is int), or compare serialised "
         "values (json.dumps, repr)."),
    Rule("RG205", "forgery.env_hijack", "Code planted outside the workspace fakes a pass", _H,
         _P,
         "sitecustomize.py, .pth import hooks or a patched pytest run before any test and can "
         "force a pass from outside the code under test.",
         "Grade in a separate verifier environment (Harbor: environment_mode = \"separate\"); "
         "run Python with -I and PYTHONNOUSERSITE=1; delete sitecustomize, usercustomize and "
         "unknown .pth files before grading."),
    Rule("RG206", "forgery.timer_tampering", "A fake clock passes a timing test", _H, _P,
         "Performance tests that read the clock inside the agent's process can be passed by "
         "patching the clock (or skipping synchronisation) instead of making the code faster.",
         "Measure time from outside the agent's code (a verifier-owned process started with "
         "python -I) and check the results as well as the speed."),
    Rule("RG207", "forgery.test_selection", "Changing which tests run fakes a pass", _H, _P,
         "Test configuration the agent controls (pytest.ini addopts and testpaths, skip or "
         "xfail marks in conftest.py) can stop the graded tests from running or failing.",
         "Run the tests with a config the agent can't edit (pytest -c /tests/pytest.ini "
         "--rootdir=/tests -p no:cacheprovider) and fail when fewer tests ran than expected."),
    Rule("RG208", "forgery.verifier_command", "The grading command can report success without "
         "passing tests", _H, _J,
         "A grader that ignores the test run's exit status, runs only part of the tests, or "
         "only checks that files exist rewards work that doesn't pass the tests.",
         "Derive the reward from the test run's real result; remove `|| true`, cache-based "
         "selection (--lf, --sw) and collect-only flags from the grading command."),
    Rule("RG210", "forgery.forgeable_report", "The grader's report is written where the agent "
         "can write", _H, _J,
         "If any scoring step trusts a report already on disk, the agent can forge one.",
         "Write the report to a path outside the agent's workspace, in the verifier only."),
    Rule("RG211", "forgery.unrestored_test_infra", "Test setup files are not restored before "
         "grading", _H, _J,
         "Files like conftest.py or pytest.ini load before any test; editing them can fake "
         "a pass.",
         "Restore test-infrastructure files from a known-good copy before grading."),
    Rule("RG213", "forgery.mutable_test_data", "The grader reads test data the agent can "
         "change", _H, _J,
         "If the expected outputs or test files the grader reads live where the agent can "
         "edit them, the agent can change the answer key instead of the code.",
         "Ship test inputs and expected outputs with the verifier (tests/), or hash-check them "
         "before grading."),
    Rule("RG212", "forgery.world_readable_tests", "The agent can read the held-out tests", _H, _P,
         "The model can read what will be checked and special-case it.",
         "chmod 700 the graded directory, or copy tests in only at grading time."),
    Rule("RG214", "forgery.test_rewrite", "The agent can rewrite the graded tests", _C, _P,
         "Graded tests the agent can edit can be emptied, and the grader then passes "
         "anything.",
         "Grade with a clean copy of the tests, copied in at grading time or restored from "
         "the verifier image."),
    Rule("RG215", "agentic.hack", "The hack agent faked a pass through the grading setup",
         _C, _P,
         "The grade passed only because of changes outside the solution's source (tests, "
         "test config, the environment), so the reward can be forged.",
         "Grade in a fresh verifier environment that receives only the solution's source "
         "files, with tests and their config copied in at grading time; leave the task out "
         "until then."),

    # -- RG3xx Is the answer leaked? --------------------------------------------------------
    Rule("RG301", "leakage.git_unreachable_objects", "The fix is recoverable from git objects",
         _H, _P,
         "The agent can read the reference solution out of the git object store.",
         "Rebuild the repo without the fix's objects (fresh clone at the base commit, gc)."),
    Rule("RG302", "leakage.cache_scan", "Compiled code of a deleted file remains", _H, _P,
         "Bytecode of removed source can be decompiled back into the answer.",
         "Ship the task without __pycache__; delete bytecode whose source was removed."),
    Rule("RG303", "leakage.cache_scan", "Patch leftover files contain the fix", _H, _P,
         ".orig / .rej files hold pre- or post-patch content verbatim.",
         "Remove *.orig and *.rej files before shipping the task."),
    Rule("RG304", "leakage.cache_scan", "A file is named like an answer", _M, _J,
         "Reference or expected output may be readable by the agent.",
         "Move reference material out of the agent's tree, or rename it if benign."),
    Rule("RG305", "leakage.mtime_clustering", "Some files were added after checkout", _M, _J,
         "`ls -lt` shows the agent which files were injected (tests, solution, answer key).",
         "Normalise modification times across the workspace when building the image."),

    Rule("RG306", "leakage.agent_env_scan", "The reference solution or tests are inside the "
         "agent's environment", _C, _P,
         "The agent can read the answer or the held-out tests straight off disk.",
         "Keep solution/ and tests/ out of the agent image; the harness copies tests in only "
         "at grading time."),
    Rule("RG307", "leakage.open_internet", "The agent has unrestricted internet access", _L, _J,
         "With open internet the agent can download the upstream fix instead of writing it.",
         "Set allow_internet = false, or route traffic through package mirrors with an allow "
         "list and request logging."),
    Rule("RG308", "agentic.hack", "The hack agent found the solution in the environment",
         _C, _P,
         "A command the agent can run prints the reference fix, so the model can copy the "
         "answer instead of writing it.",
         "Remove the source the command reads (history, caches, files, network access) from "
         "the image the agent runs in."),

    # -- RG4xx Are the tests fair? ----------------------------------------------------------
    Rule("RG401", "test_quality.overfit_equivalence", "A test depends on wording the task "
         "never specifies", _H, _P,
         "A correct solution that words a message differently fails, so correct work is "
         "punished.",
         "Assert the behaviour, not the incidental string; or state the string in the task."),
    Rule("RG402", "test_quality.symbol_crossref", "A test uses an internal name the task never "
         "mentions", _M, _J,
         "A correct solution with different internals fails the test.",
         "Test through the public interface, or name the symbol in the instruction."),
    Rule("RG403", "test_quality.brittle_assertions", "A test pins the reference's exact wording",
         _M, _J,
         "A correct solution that phrases output differently fails.",
         "Assert the behaviour, not the reference's exact wording."),
    Rule("RG404", "test_quality.brittle_assertions", "A test pins an exact call count", _M, _J,
         "A correct solution with a different call shape fails.",
         "Assert observable behaviour or outputs, not the number of internal calls."),
    Rule("RG405", "review.tests", "A test checks something the task doesn't ask for", _H, _J,
         "A solution that does exactly what the statement says fails, so correct work is "
         "punished.",
         "Change the test to match the statement, or state the requirement in the task."),
    Rule("RG406", "review.tests", "A test asserts nothing meaningful", _M, _J,
         "A test that only checks the code runs or imports gives reward for any change.",
         "Assert the outputs, state or effects the change is meant to produce."),
    Rule("RG408", "agentic.ambiguity", "The grader rejects a correct solution the task allows",
         _H, _P,
         "A solution that does what the statement asks fails, so correct work is punished.",
         "State the detail in the task, or broaden the test; otherwise leave the task out."),
    Rule("RG407", "review.tests", "A test rejects a correct solution written differently",
         _H, _J,
         "The test pins a detail of the reference the statement leaves open, so other "
         "correct solutions fail.",
         "Assert the behaviour the statement asks for, or state the detail in the task."),

    # -- RG4xx, rubric-graded tasks (reports, spreadsheets, documents) ---------------------
    Rule("RG409", "rubric.static", "A rubric criterion is not atomic or observable", _M, _J,
         "A criterion that bundles several checks, or uses words like \"good\" or "
         "\"appropriate\", is graded inconsistently, so the reward is noisy.",
         "Split it into criteria that each check one thing a grader can see in the "
         "deliverable."),
    Rule("RG410", "rubric.static", "Rubric criteria repeat, or weights don't match "
         "consequence", _L, _J,
         "Duplicate criteria count one thing twice, and flat weights make a cosmetic slip "
         "cost as much as a wrong answer.",
         "Merge duplicates; weight criteria by how much a mistake would matter, and add "
         "penalty criteria for serious errors."),
    Rule("RG411", "rubric.static", "A requirement in the task has no rubric criterion", _M, _J,
         "Work the task asks for that the rubric never checks earns nothing, so the model "
         "learns to skip it.",
         "Add a criterion for each requirement the statement makes."),

    # -- RG5xx Is the reward wired correctly? (A1–A7) ---------------------------------------
    Rule("RG501", "reward_shape.conformance", "A held-out signal never reaches the training "
         "reward", _H, _P,
         "Held-out coverage is computed but gates nothing.",
         "Route the held-out signal into the training reward, as a gate or a weighted term."),
    Rule("RG502", "reward_shape.conformance", "Milestone weights are defined but unused", _M, _P,
         "Cosmetic weights mislead anyone reading the reward spec.",
         "Apply the milestone weights in the reward, or delete them."),
    Rule("RG503", "reward_shape.conformance", "A weight differs across scoring files", _M, _P,
         "Which value applies depends on which file is read; the reward is ambiguous.",
         "Make every scoring file declare the same weight."),
    Rule("RG504", "reward_shape.conformance", "A multiplicative gate can zero the whole reward",
         _M, _P,
         "One failing gate wipes out all partial credit, giving a sparse, cliff-shaped reward.",
         "Use an additive term or a soft gate unless the cliff is intended."),
    Rule("RG505", "reward_shape.conformance", "A participation term depends on feature success",
         _M, _P,
         "Credit meant for attempting is only paid when the feature already works.",
         "Compute the participation term independently of feature success."),
    Rule("RG506", "reward_shape.conformance", "The reward-hacking heuristic points the wrong way",
         _H, _P,
         "The degenerate hack is not flagged, while honest runs may be.",
         "Flag the direction the degenerate hack actually moves the metrics."),
    Rule("RG507", "reward_shape.conformance", "Scoring files contradict each other", _M, _P,
         "Contradictory metadata makes the reward definition ambiguous.",
         "Make the metadata agree across every scoring file."),

    # -- RG6xx Is the build reproducible? ---------------------------------------------------
    Rule("RG601", "hygiene.dockerfile", "COPY/ADD pulls the whole build context into the image",
         _H, _J,
         "A broad copy can drag held-out tests or the reference solution into the agent image.",
         "Copy only the needed paths; keep tests in a separate verifier image."),
    Rule("RG602", "hygiene.dockerfile", "The base image is not pinned", _M, _J,
         "An untagged or :latest base changes under you; builds aren't reproducible.",
         "Pin the base image by tag or @sha256 digest."),
    Rule("RG603", "hygiene.dockerfile", "The build depends on the host's CPU count", _M, _J,
         "nproc varies by host, so build and test parallelism are non-deterministic.",
         "Hardcode the parallelism or derive it deterministically."),
    Rule("RG604", "hygiene.dockerfile", "A dependency is given as a version range", _L, _J,
         "A range resolves differently over time, so builds aren't reproducible.",
         "Pin exact versions (pkg==x.y.z) or use a locked requirements file."),
    Rule("RG606", "hygiene.dockerfile", "Dependencies install after the source is copied",
         _L, _J,
         "Every change to the task's files re-runs the dependency install, so images rebuild "
         "slowly and can pick up different versions each time.",
         "Copy only the dependency manifests (requirements.txt, package.json, …) and install "
         "them before copying the rest of the source."),
    Rule("RG607", "hygiene.grader_downloads", "The grader downloads software while grading",
         _L, _J,
         "A grader that installs packages at grading time needs open network and can get "
         "different versions on different runs.",
         "Install the grader's tools in the verifier image at build time, pinned."),
    Rule("RG608", "baseline.build", "An image the task uses does not build", _C, _P,
         "A task whose environment can't be built can't be trained on at all.",
         "Fix the build error shown in the finding, then run the check again."),
    Rule("RG605", "hygiene.harbor_compat", "The task can't run the way its harness runs it",
         _M, _P,
         "A task that its own harness can't run, or runs differently, trains on a different "
         "task than the one that was checked.",
         "Fix the task files named in the finding so the harness can run the task as "
         "written."),

    # -- RG7xx Is the task clear? (L2 static review, LLM, --deep-review) ---------------------------
    Rule("RG701", "review.statement", "The task statement doesn't say what to change", _H, _J,
         "A solver can't tell what is wanted or when it is done, so the reward rewards "
         "guessing.",
         "State the change to make and how to tell it is done."),
    Rule("RG702", "review.statement", "The task statement gives away the solution", _H, _J,
         "The solver copies the answer instead of working it out, so training teaches "
         "copying.",
         "Describe the behaviour wanted; take out the code and the exact lines to change."),
    Rule("RG703", "review.statement", "The task statement depends on material the solver "
         "can't see", _H, _J,
         "The statement points at an attachment, image or discussion that isn't there, so "
         "part of the task is unknowable.",
         "Put what that material says into the statement itself."),
    Rule("RG704", "review.determinability", "A test checks something the solver can't know",
         _H, _J,
         "A correct solution fails because the test expects a name, value or format that "
         "neither the statement nor the repository gives.",
         "State that detail in the task, or loosen the test so any reasonable choice "
         "passes."),
]

# A1–A7 (reward_shape.conformance's internal codes) → rule IDs.
REWARD_CODE_RULES = {f"A{i}": f"RG50{i}" for i in range(1, 8)}

RULES: dict[str, Rule] = {r.id: r for r in _RULES}
if len(RULES) != len(_RULES):  # pragma: no cover - import-time guard against a typo
    raise RuntimeError("duplicate rule id in the catalogue")


def get(rule_id: str) -> Rule:
    try:
        return RULES[rule_id]
    except KeyError:
        raise KeyError(f"unknown rule id {rule_id!r}; add it to rlgate/core/rules.py") from None


def for_probe(probe_id: str) -> list[Rule]:
    return [r for r in _RULES if r.probe_id == probe_id]


def default_rule(probe_id: str) -> str | None:
    """The rule a probe's findings get when the probe didn't set one: only defined for
    probes that emit exactly one rule (multi-rule probes must set ``rule_id`` themselves)."""
    rs = for_probe(probe_id)
    return rs[0].id if len(rs) == 1 else None


def by_step() -> dict[Step, list[Rule]]:
    out: dict[Step, list[Rule]] = {s: [] for s in Step}
    for r in _RULES:
        if r.step is not None:
            out[r.step].append(r)
    return out
