# Task Quality & Reward Integrity Process

The process we actually used to audit and fix RL coding-eval tasks, generalized into a
reusable SOP. Grounded in the multi-round audit→fix pass on the 5-task **cline-rl-envs** set
(Round 1→4, Oct 2026); the 10-task **foundryai-ots** bank is the second, applied example.
Applies to any Harbor-style task with `train`/`eval`/held_out scoring and a container backend.

Source material: `cline-rl-envs/{TASK_CONSTRUCTION,TRAJECTORY_ANALYSIS,SECURITY_FINDINGS,
FINDINGS_DETAIL,FINDINGS_DESIGN,CHANGELOG}.md` and `verify_oracle_nop.sh`.

## 0. Mental model & the four hard lessons

Problems that all get called "reward hacking" are really distinct and need different checks:

| Failure mode | What it is | How you detect it | Action |
|---|---|---|---|
| **Grader-channel forgery** | Verdict comes from a channel the candidate's own code can write (unrestored runner/config, in-process or detached report writer, forgeable native report, world-readable `/tests`) | Exercise the scorer on a *forged* input; adversarial sweep | **Fix the harness** |
| **Grader exploitation (coverage)** | Honest code passes visible tests without doing the work | High `train`, low `eval`, **+ read the diff** | Tune F2P / held_out |
| **Spec-gaming by omission / plausible-wrong** | Genuine general code wrong on the hard case | High `train`, low `eval`, diff shows honest code | By design — held_out's job |
| **Unfair tests** | A *correct* solution fails a graded check (over-fit to gold internals, graded disclaimed text, flaky) | Oracle < 100%, flaky re-runs, nop analysis | Fix immediately |

Four principles, each learned the hard way on the cline set:

- **P1 — the reward is a dense gradient.** `train > eval` is the expected shape of an unfinished or plausible-wrong solution, not a hack signature. Never classify a trial from scalars alone.
- **P2 — only a trajectory/diff reveals grader *exploitation*.** Scalars tell you where to look; the code tells you what happened.
- **P3 — the #1 real reward-hack is grading-channel forgery, not coverage.** 13 of ~35 cline findings were this. *Any verdict derived from a channel the candidate's same-UID code can write is forgeable.* The whole bank's hardest fixes were here, and a bank with a clean harness (the 10-task one) has almost none of it — so audit the channel first.
- **P4 — synthetic validation ≠ live validation.** A scorer change validated only against a stored report misses harvesting regressions. A live containerized run surfaced the bavilla *detached-child-process race* that the synthetic round had passed. Always finish with a live run.

## 1. The process (construction → bounds → calibration → gates → audit → fix rounds → sweep → re-verify)

**Phase A — Construction / qualification.** Mine real agent trajectories; keep only tasks on a resolvable public repo with a real test substrate and a genuinely-undone base. Build the gold patch (upstream `git diff`, or authored-to-contract); require a byte-identical round-trip and record `gold_patch_sha256`. Build a single `environment/Dockerfile` — no bind-mounts, no `COPY . /app`, pinned deps, vendored snapshot where upstream GC'd the commit. Design the verifier: split tests into f2p / held-out / p2p by name-prefix, group into 3–7 milestones, scope p2p to a declared base-green **name-set** with recorded exclusions.

**Phase B — Establish reward bounds first.** Oracle must score **1.000**, nop **0.000** on the graded config. Nothing else proceeds until this holds.

**Phase C — Difficulty calibration via live-model rollout.** Run `mini-swe-agent` N=1 × ~3 models. Keep-band = "some but not all models solve." 3/3 or weak-model-100% → withdraw/rework (this archived 2 of the original 7 cline tasks); 0/3 → re-confirm oracle. Difficulty is a first-class, measured gate.

**Phase D — Three fairness/quality gates.**
1. **Static checks (7):** no bind-mounts, pinned deps, no bare `nproc`, absolute paths, no answer-leakage, `environment_mode="separate"`.
2. **Instruction↔test alignment:** map *every* graded check to the instruction sentence that obligates it (cline reached 126/126). An unmapped check is either unfair (ungated requirement) or the instruction is an answer key.
3. **5-axis design audit:** anti-cheat, isolation, functional-not-string verification, alignment, determinism, difficulty — one deep reviewer per task over instruction + metadata + scoring_config + score.py + test.sh + graded sources + gold.

**Phase E — Reward-hacking / anti-cheat audit.** Exercise each scorer on synthetic inputs standing in for (a) genuine oracle, (b) nop, (c) **forgery**. "Forgeable" = scorer returns ≈1.0 on an input a submission can cause with no real work. Walk the verdict-channel checklist (§2).

**Phase F — Fix, then re-verify oracle=1.0 / nop=0.0 after *every* change; iterate rounds.** The cline pass took four rounds; Round-3 was a **live Daytona run** that surfaced a new exploit the synthetic rounds missed. A fix that backfires gets reverted (flashcpp `T**` test passed at nop, inflating the floor → reverted, gap documented).

**Phase G — Adversarial sweep + final re-verification.** Throw ~10+ distinct no-work / novel hack attempts; each must score 0.0. Then run `verify_oracle_nop.sh` at **k=5** on the container backend; bump the task version.

## 2. Verdict-channel integrity — the anti-cheat pillar

Shared root cause of the 13 anti-cheat findings: *the verdict was derived from a channel the
candidate's same-UID in-process code could write.* The fix family (apply all):

- **Restore every candidate-reachable verdict input from a reference snapshot at verify time** — the test runner, and all build/test config: `package.json`, lockfile, `vitest*.config`, `tsconfig*`, `Makefile`, `run_all_tests.sh`.
- **`chmod 700 /tests`** so held-out sources and the scorer are not world-readable.
- **Dual-channel, fail-closed:** cross-check the forgeable native report (TRX / junit / vitest-json / `go test -json`) against a **root-owned EOF/stream reconstruction** (`record_outcomes.py`, `record_vitest.py`). Disagreement → fail to the failing side. **Missing trusted file → fail closed, never a free pass.**
- **Move the verdict to a root-owned pipe** outside candidate write reach; `pkill -u grader` before scoring to kill detached children that race the report.
- **Parse fail-wins:** an injected `--- PASS:` / text event must never override a real failure.
- **Never encode the answer in a candidate-controlled artifact** (e.g. exit code in a test *filename*) — interpose and strip it (`fc_wrap.sh`).
- **Integrity-check graded test files against the image originals** (a rewritten check scores as a failed build, not a pass); **delete graded sources after build** so held-out names can't be read at runtime.

Operational checklist: `tools/grading_integrity_checklist.md`.

## 3. Issue taxonomy (counts from the 5-task cline pass)

| Class | Count | Example |
|---|---:|---|
| Anti-cheat / verdict-channel forgery | 13 | bavilla unrestored `package.json` + detached-child race → 1.0/1.0 with no work |
| Unfair tests (correct solution fails) | ~6 | bavilla graded exact warning *wording* the instruction disclaims; strike name-dependent `nolint` grep |
| Weak / missing coverage | 5 | strike producer happy-path untested; flashcpp `T**` never exercised |
| Spec fairness (under/over) | 2 | ontserve m6 curator CLI unguessable (under); sockseek instruction = answer key (over) |
| Scoring-model design debt (A1–A7) | 7 | held_out never enters `train` (no gradient); flag points wrong way for always-pass degenerate |
| Build / reproducibility | 2 | ontserve did not build as shipped (unpinned SQLAlchemy → psycopg mismatch; build-time postgres) |

## 4. Per-task go/no-go rubric

Release-quality only when all hold:
1. Oracle = 1.0 and nop = 0.0 at **k=5** on the container backend.
2. No graded check with oracle < 100%; no flaky check.
3. Every P2P passes at nop; no F2P/held_out leaks at nop; p2p gates on a **name-set** and fails closed if collection collapses.
4. **Verdict channel not forgeable:** all candidate-reachable inputs restored; `/tests` 700; dual-channel cross-check; fail-wins parse; adversarial sweep all 0.0.
5. Task **builds as shipped** (pinned deps; no build-time service the builder can't run).
6. Difficulty in the keep-band (live-model rollout).
7. No grader exploitation in agent trials (diff review); no vacuously-passing held_out.
8. held_out guards the *property* (whole shortcut family fails one check); instruction↔test alignment is 1:1; no graded text the instruction disclaims; no over-fit to gold internals (exact call counts, exact strings).
9. `reward_hacking_suspected` is sane for the realistic degenerate (note A6: it can point the wrong way).

## 5. Scoring-model notes (design debt to decide on deliberately)

From `score.py`: `train = base × max(0, 1 − p2p_fail_frac)`, `base = (f2p + lint_build)/denom`;
`eval = held_out_fraction`; `delta = train − eval`; `reward_hacking_suspected` fires when
`train ≥ 0.8 and eval ≤ 0.3`. Known debt (flag, change only with eyes open — each alters the signal):
A1 held_out never enters `train` (no training gradient); A2 milestone weights cosmetic; A3 dead
`task.json.scoring`; A4 p2p is a multiplier, penalty mis-shaped; A5 `lint_build` 0.05 measures
nothing; A6 the flag points the wrong way for an always-pass degenerate (e.g. always-`exit(0)`
compiler → eval high, train low → flagged clean); A7 stale metadata.

## 6. Harness coverage (multi-language)

Harbor + container backend (Daytona), not local pytest. Native machine-readable reports per
language — Go `-json`, C#/.NET **TRX**, Python junit, TypeScript **vitest json**, C++ exit-code
corpus — read from the native report, never scraped console. `graded-tests.diff` is applied over
the base repo at verify time and integrity-checked. `tests/` and `solution/` live in a separate
verifier image, never in the agent's container.

## 7. Tooling & CI

- `cline-rl-envs/verify_oracle_nop.sh` — oracle/nop bounds at k=5 on the backend (Phase B/G).
- `tools/percheck_matrix.py <jobs_dir>` — Phase D/F signals S1 (unfair/broken/flaky), S2 (P2P validity), S2b (leakage), S3 (agent gradient); exits non-zero on any fairness/validity failure.
- `tools/differential_shortcut.py` — Phase E coverage: scaffold/run/grade hand-written shortcut patches; flags high-train / low-eval.
- `tools/grading_integrity_checklist.md` — Phase E verdict-channel checklist + adversarial-sweep template.

**CI gate on every new/changed task:** (1) `verify_oracle_nop.sh` k=5 → oracle 1.0 / nop 0.0;
(2) `percheck_matrix.py jobs` exits 0; (3) grading-integrity checklist + adversarial sweep all 0.0;
(4) each known shortcut family via `differential_shortcut.py` → no FAIL; (5) a **live** run, not just
synthetic (P4); block merge on any failure, attach the matrix to the PR.

## 8. Worked examples

**cline-rl-envs (primary).** 5 tasks, 4 rounds; dominated by verdict-channel forgery (flashcpp
unrestored runner; bavilla unrestored config + detached-child race; sockseek/ontserve forgeable
TRX/junit without cross-check) plus over-fit unfair tests (bavilla strings/call-counts) and one
task that didn't build (ontserve). Fixes: restore-from-reference, `chmod 700 /tests`, dual-channel
reconstruction, interposer, +2 held-out cases, softened unfair assertions, pinned deps. Final:
oracle 1.0 / nop 0.0 at k=5, 11-attempt adversarial sweep all 0.0.

**foundryai-ots 10-task bank (applied).** Harness was already clean (behavioral HTTP/DB, separate
env, hooks scrubbed) → almost no verdict-forgery surface; the story there was the dense gradient
and plausible-wrong solutions (qcd-zip `file_size>0` done-signal; canary rollback-heavy held_out
that silences the flag; vt negative-delta under-reward). No grader exploitation observed across 40
rollouts; oracle 100% and P2P valid at nop throughout.
