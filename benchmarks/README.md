# Benchmarks with published flaws

This folder is a labelled test set for rlgate built from **public audits of RL environments and coding benchmarks**. Every entry is a real task that a named source (a benchmark maintainer's changelog, a paper, an issue report, a reviewed audit) says is broken in a specific way: tests too weak or too strict, an underspecified statement, a leaked answer, a forgeable grader, a gold patch that fails, a no-op that passes, a flaky or broken environment, or an impossible task. Where a fixed version of the task exists it is included as a **control**, which must come back clean on the rules that the fix addressed.

372 labelled positives and 46 controls across 22 benchmarks, plus bulk label sets (thousands of rows) referenced by URL. No task content is copied here: `fetch.py` downloads tasks from their upstream repos at pinned versions.

## Per-benchmark table

| Benchmark | Version(s) | Labelled tasks | Controls | Smoke / core | Bulk sets | Audits linked | Runnable by rlgate |
|---|---|---|---|---|---|---|---|
| [bigcodebench](bigcodebench/) | HF bigcode/bigcodebench split v0.1.0_hf (positives); split v0.1.4 (twins) | 7 | 0 | 0 / 4 | 0 | 1 | yes |
| [cheatbench](cheatbench/) | github.com/centerforaisafety/cheatbench tasks/software_engineering/instances.json | 0 | 0 | 0 / 0 | 1 | 1 | partial |
| [deepswe](deepswe/) | deep-swe@0b9fabbb63b9 | 42 | 0 | 1 / 10 | 1 | 2 | yes |
| [humaneval-plus](humaneval-plus/) | HF openai/openai_humaneval | 1 | 0 | 0 / 1 | 0 | 1 | yes |
| [impossiblebench](impossiblebench/) | HF fjzzq2002/impossible_swebench | 10 | 4 | 2 / 9 | 2 | 1 | yes |
| [mbpp-plus](mbpp-plus/) | HF google-research-datasets/mbpp (full); MBPP+ v0.2.0 (twins) | 10 | 0 | 0 / 5 | 1 | 1 | yes |
| [mimo](mimo/) | HF XiaomiMiMo/MiMo-V2.6-RL-oss code config; images xiaomimimo/mimo-v2.6-rl-oss:<instance_id> | 0 | 0 | 0 / 0 | 1 | 1 | yes |
| [multi-swe-bench](multi-swe-bench/) | HF ByteDance-Seed/Multi-SWE-bench | 9 | 0 | 0 / 4 | 1 | 1 | partial |
| [multi-swe-rl](multi-swe-rl/) | HF ByteDance-Seed/Multi-SWE-RL data_20240601_20250331; HF PrimeIntellect/Multi-SWE-RL-Verified | 14 | 0 | 1 / 6 | 2 | 2 | partial |
| [openswe](openswe/) | HF GAIR/OpenSWE (gated) | 2 | 0 | 0 / 2 | 0 | 1 | partial |
| [r2e-gym](r2e-gym/) | HF PrimeIntellect/R2E-Gym-Subset-Verified (split dropped) | 1 | 0 | 0 / 1 | 1 | 1 | partial |
| [swe-bench](swe-bench/) | HF SWE-bench/SWE-bench, SWE-bench_Lite (images :v1 only) | 13 | 0 | 0 / 5 | 2 | 3 | yes |
| [swe-bench-multilingual](swe-bench-multilingual/) | HF SWE-bench/SWE-bench_Multilingual | 11 | 3 | 0 / 5 | 2 | 2 | yes |
| [swe-bench-pro](swe-bench-pro/) | HF ScaleAI/SWE-bench_Pro config v1 (tag v1.0); scaleapi/SWE-bench_Pro-os v2/tasks (V2) | 21 | 3 | 1 / 15 | 5 | 8 | partial |
| [swe-bench-verified](swe-bench-verified/) | HF SWE-bench/SWE-bench_Verified; images swebench/sweb.eval.x86_64.*:v1 (leaky) and :v2 (fixed) | 56 | 13 | 5 / 23 | 6 | 7 | yes |
| [swe-gym](swe-gym/) | HF SWE-Gym/SWE-Gym | 9 | 2 | 0 / 5 | 1 | 2 | yes |
| [swe-rebench-v2](swe-rebench-v2/) | HF nebius/SWE-rebench-V2; HF PrimeIntellect/SWE-rebench-V2-Filtered-Verified (validation files) | 24 | 0 | 0 / 6 | 1 | 2 | partial |
| [swe-smith](swe-smith/) | HF SWE-bench/SWE-smith, -py, -rs | 3 | 0 | 0 / 3 | 1 | 1 | partial |
| [terminal-bench-1](terminal-bench-1/) | terminal-bench-1 (refs per entry; several 'main' to be pinned at fetch) | 15 | 0 | 0 / 3 | 0 | 1 | partial |
| [terminal-bench-2](terminal-bench-2/) | terminal-bench-2@69671fbaac6d (2.0 registry pin); terminal-bench-2-1@12b7565679ce (2.1, controls) | 63 | 19 | 10 / 33 | 4 | 12 | yes |
| [terminal-bench-4](terminal-bench-4/) | terminal-bench@v4.0.0 (452bf305); terminal-bench@v3.0.0 (2b0442c3); terminal-bench@v4.1.0 (4070d6ce, twins) | 58 | 2 | 1 / 19 | 4 | 6 | yes |
| [terminal-wrench](terminal-wrench/) | terminal-wrench@d8a29613235a | 3 | 0 | 0 / 3 | 1 | 1 | yes |
| [review-only](review-only/) | tau2, OSWorld, WebArena, BFCL, KernelBench, HLE, … | reference only | | | | | no |

Runnable: **yes** = rlgate reads the fetched layout directly (Harbor dirs including DeepSWE, `--layout swebench`, or a generated `task.rlgate.yaml`); **partial** = rows are fetched but need a mapping YAML (`--layout file`) for a non-pytest grader or image naming that has not been checked.

## Entry fields (`<benchmark>/labels.yaml`)

| Field | Meaning |
|---|---|
| `id` | entry id (unique across the folder); the upstream task id is inside it and in `fetch` |
| `benchmark`, `version` | the benchmark release and the exact commit, tag, HF revision or image tag |
| `tiers` | `smoke` (~20, cheap, all categories, positives + controls) ⊂ `core` (~160) ⊂ `all` |
| `polarity` | `positive` (flawed) or `negative` (control) |
| `category` | `weak_tests`, `too_strict`, `underspecified`, `answer_leak`, `forgeable_grader`, `gold_fails`, `nop_passes`, `flaky`, `broken_env`, `impossible`; `other_categories` lists secondary ones |
| `label` | the flaw, close to the source's own words |
| `expected_rules` | rlgate rule ids that should fire (see `docs/rules.md`) |
| `needs: deep-review` | every expected rule is found only by the LLM review (L2–L4); score these only on `--deep-review` runs |
| `clean_for` | on a control: the rules that must not fire |
| `confidence` | high = human-verified, execution-confirmed, or two sources agree; medium = one source; low = LLM-only or second-hand |
| `source` | one or more `{org, url}`; every entry cites at least one public URL |
| `fetch` | how `fetch.py` gets it: `git` (repo, ref, path) or `hf_row` (dataset, id or row index, image tag) |
| `control` | the twin entry id, or where the fixed version lives |

Each folder may also have a `bulk:` block: a whole upstream label file (vendored under `upstream/` or linked by URL) plus a filter rule mapping its labels to categories and rules. Bulk sets are for wide recall runs and are not expanded into entries.

## Fetch

```
python benchmarks/fetch.py all --tier smoke --dry-run        # list + approximate sizes, no network
python benchmarks/fetch.py all --tier smoke                  # git exports + HF rows into ./rlgate-benchmarks/
python benchmarks/fetch.py swe-bench-verified --tier core --pull-images
python benchmarks/fetch.py all --tier all --dry-run          # also counts the bulk label sets
```

Needs Python 3, PyYAML and git; `huggingface_hub` (+ `pyarrow`) for HF rows; `docker` only with `--pull-images`. Resolved commits and revisions are written to `<out>/LOCK.json`. The smoke tier is about 65 MB of git exports and 36 MB of HF data; its 8 SWE images are about 22 GB if pulled. OpenSWE is gated on Hugging Face (accept its terms first).

## Run

```
rlgate check rlgate-benchmarks/terminal-bench-2 -j 8
rlgate check rlgate-benchmarks/deepswe -j 8
rlgate check rlgate-benchmarks/swe-bench-verified -j 8
```

Each folder's README states the layout. Scoring a single-function benchmark (MBPP, HumanEval, BigCodeBench) uses the generated `task.rlgate.yaml` per task.

## Score

- **Recall (per category, per tier):** a positive counts as found if any rule in `expected_rules` is reported (Proven or Needs-your-judgment). Report deep-only entries separately: they can only be found with `--deep-review`.
- **Precision of expected rules:** among findings on positives, the share whose rule is in that entry's `expected_rules`. Other findings on a positive are *unlabelled*, not false positives: audits only report what they looked for.
- **Controls must stay clean:** a control fails if any rule in its `clean_for` fires. That is the false-positive rate. Findings outside `clean_for` are not scored, because a fixed twin can still carry other, unrelated flaws.
- Report confidence tiers separately; low-confidence labels (LLM-only audits, second-hand reports) are for triage, not for headline numbers.

## Score and publish

```
uv run --no-project --with pyyaml python benchmarks/score.py ~/rlgate-bench-runs/smoke-nollm --tier smoke
uv run --no-project --with pyyaml python benchmarks/score.py ~/rlgate-bench-runs/smoke-nollm --tier smoke \
    --publish benchmarks/results/0.1.1/smoke-no-llm
```

The first prints the score. The second writes `SCORE.md`, `score.json` and each task's report
into [results/](results/), with local paths removed (it refuses if any are left). Outcomes:
**found**, **warning** (only as a warning), **missed**, **not run** (rlgate couldn't check it;
the error is kept), **deep-only** (needs `--deep-review`), **absent** (not in these runs); a
control is **clean** or **failed**.

## Caveats

- **Labels are what the sources claim.** Medium and low confidence entries rest on one report; some are open issues the maintainers have not confirmed. Conflicts between sources are noted in `note` (e.g. Terminal-Bench 4 `formal-crypto` and `risk-scorer-replay`: Preference Model found no issues, other reports did; SWE-bench Verified `django__django-10097`: ABA major vs PAIChecker clean).
- **Audits are not exhaustive.** A task missing from a list is not proven clean. Controls are fixed twins or tasks that several independent audits called clean, and are clean only for their `clean_for` rules.
- **Image versions matter.** SWE-bench `:v1` images (before September 2025) keep future git history (#465); `:v2` do not. Non-Verified SWE-bench instances only have `:v1`. SWE-bench Pro v1 images keep fix commits; V2 rebuilt them.
- **MiMo:** Vals AI reported that about two-thirds of the MiMo-V2.6 code tasks leaked the answer, but published no id list, and the images were re-pushed on 2026-10-16. The current code-task images are public and rlgate runs them (`--layout mimo`), but the leaky versions can't be pulled, so MiMo is a bulk reference only.
- **Second-hand material:** OpenAI's 2026 SWE-bench Verified post could not be fetched directly; the two ids attributed to it come via secondary write-ups and only appear alongside other sources. Epoch AI review text was paraphrased by the fetch tool; labels follow the review's classification, not exact wording.
- **Some ids need resolving at fetch time:** SWE-rebench-V2 short ids (from HF discussion #7) are suffix-matched; ImpossibleBench rows are selected by row index and paired with the `original` row of the same instance; Multi-SWE-bench #57 ids are given as `org/repo:pr-N`. `fetch.py` refuses ambiguous matches.
- **Layouts:** rlgate's `swebench` preset runs `python -m pytest -q` and does not apply `test_patch` / `FAIL_TO_PASS`; non-Python and Pro v1 rows need a mapping YAML. Terminal-Bench 1 tasks use the legacy format.
- **Some image names are second-hand** (SWE-Gym `xingyaoww/*`, Multi-SWE `mswebench/*`); check them before pulling.

## Licences

The labels here are facts (task id, flaw, source) with citations. Third-party label files are copied only when their licence allows it (MIT) and they are small; each `upstream/README.md` gives attribution and sha256. Copied: PAIChecker human labels for SWE-bench Verified (id + label columns only), Multilingual and SWE-Gym (MIT); two id lists *derived* by diffing public MIT-licensed releases (UTBoost-changed SWE-bench instances; SWE-bench Pro v1 ids dropped in V2); Amazon's tau2-bench-verified `FIXES.md` (MIT). Everything else (OpenAI's annotations, Gabor's audits, Preference Model, Tokenless, OpenCompass, ABA, the OSWorld fix log) is linked, not copied. Task content stays upstream under each benchmark's own licence. See [SOURCES.md](SOURCES.md).
