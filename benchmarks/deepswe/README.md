# deepswe

DeepSWE (Datacurve) Harbor tasks with execution-confirmed grader defects (Tokenless) and reviewed false negatives (Epoch AI).

## Versions

- deep-swe@0b9fabbb63b9

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Tokenless](https://github.com/tokenless-ai/envcheck-findings/tree/post/2026-10-grader-defects) | EnvCheck: 110 reward hacks and grading bugs (findings.jsonl, execution-confirmed cases): 13 TB4 + 21 DeepSWE + 19 BFCL tasks (163 cases) | 2026-09-30 | high |
| [Epoch AI](https://epoch.ai/benchmarks/deepswe/review) | DeepSWE v1.1 benchmark review (23 FN, hand-confirmed): 23 | 2026-09-07 | medium |

## Counts

- 42 labelled positives, 0 controls; tiers: smoke 1, core 10, all 42.
- Categories: too_strict 19, weak_tests 11, forgeable_grader 10, underspecified 1, broken_env 1.
- Most expected rules: RG211 (26), RG408 (23), RG110 (13), RG101 (6), RG201 (5), RG207 (2), RG108 (2), RG704 (2).
- Entries that only the deep review can find (`needs: deep-review`): 5.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- `rlgate check <out>/deepswe --layout deepswe -j 8`
- Fetch: `python benchmarks/fetch.py deepswe --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `tokenless-findings`: 82 rows / 163 cases; 13 TB4 + 21 DeepSWE tasks - https://github.com/tokenless-ai/envcheck-findings/tree/post/2026-10-grader-defects
