# terminal-bench-4

Terminal-Bench monorepo: 4.0.0 tasks flagged by reviews and issue reports, plus 3.0.0 tasks removed in 4.0 for defects. 4.1.0 fixed some tasks (per-task README change-log).

## Versions

- terminal-bench@v4.0.0 (452bf305)
- terminal-bench@v3.0.0 (2b0442c3)
- terminal-bench@v4.1.0 (4070d6ce, twins)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Epoch AI](https://epoch.ai/benchmarks/terminal-bench-4/review) | Terminal-Bench 4.0.0 benchmark review (19 FP, 11 FN): 30 tasks | 2026-09-04 | medium |
| [Preference Model](https://preferencemodel.com/blog/terminal-bench-4-is-saturated/issues/) | Terminal Bench 4 Is Saturated + per-task issues report: 242 findings over 61 tasks | 2026-10-19 / 2026-10-20 | medium |
| [Tokenless](https://github.com/tokenless-ai/envcheck-findings/tree/post/2026-10-grader-defects) | EnvCheck: 110 reward hacks and grading bugs (findings.jsonl, execution-confirmed cases): 13 TB4 + 21 DeepSWE + 19 BFCL tasks (163 cases) | 2026-09-30 | high |
| [harbor-framework/terminal-bench maintainers + reporters](https://github.com/harbor-framework/terminal-bench/issues) | Issue tracker (563 issues, dump harbor_terminal-bench_issues_2026-11-11.tsv): 402 issues naming TB4 tasks | 2026-02-24 .. 2026-11-09 | medium |
| [Vals AI](https://www.vals.ai/blogs/mimo-reward-hacking) | Two-Thirds of MiMo v2.6's Coding Tasks Leak the Answer: 1,795/2,698 leaky (no id list); names sglang-qwen-burst | 2026-10-07 | medium |
| [Terminal-Bench maintainers](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.0.0) | TB 4.0.0 release notes: tasks removed from 3.0 / modified: 8 removed + 19 modified | 2026-08-26 | high |

## Counts

- 58 labelled positives, 2 controls; tiers: smoke 1, core 19, all 60.
- Categories: forgeable_grader 20, weak_tests 11, answer_leak 7, too_strict 5, underspecified 5, flaky 4, broken_env 4, gold_fails 2.
- Most expected rules: RG110 (22), RG704 (9), RG408 (8), RG212 (8), RG003 (8), RG215 (7), RG111 (7), RG306 (6).
- Entries that only the deep review can find (`needs: deep-review`): 19.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- Harbor task dirs: `rlgate check <out>/terminal-bench-4 -j 8`
- Fetch: `python benchmarks/fetch.py terminal-bench-4 --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `pm-tb4-findings`: 242 findings, 61 tasks (+3 benchmark-wide) - https://preferencemodel.com/blog/terminal-bench-4-is-saturated/issues/
- `tokenless-findings`: 82 rows / 163 cases; 13 TB4 + 21 DeepSWE tasks - https://github.com/tokenless-ai/envcheck-findings/tree/post/2026-10-grader-defects
- `harbor-tb-issues`: 563 issues (402 name a TB4 task) - https://github.com/harbor-framework/terminal-bench/issues
- `tb4-v4.1-changelogs`: 66 tasks touched - https://github.com/harbor-framework/terminal-bench/tree/v4.1.0/tasks

## Caveats

- `tb4-risk-scorer-replay`: CONFLICT: PM lists it with no findings
- `tb4-formal-crypto`: CONFLICT: PM lists it with no findings. Tokenless reproduced at 1dcda8716784493721921c23e4bc7f7d988b4494
- `tb3-vba-userform-port`: #1375 version not stated in the notes (v3.0.0 or v4.0.0); verify which tag lacks the reward write
