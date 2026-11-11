# swe-bench

SWE-bench full test split and Lite: instances OpenAI's 2024 annotators filtered out of Verified, and UTBoost's Lite-only weak tests.

## Versions

- HF princeton-nlp/SWE-bench, SWE-bench_Lite (images :v1 only)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [OpenAI Preparedness + SWE-bench authors](https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip) | SWE-bench Verified human annotations (3 annotators, ensembled): 1,699 annotated (1,160 filter_out) | 2024-08-12 | medium |
| [CUHK-SZ / UIUC (Kang lab)](https://github.com/CUHK-Shenzhen-SE/UTBoost/blob/main/assets/useful_scripts/augTest.json) | UTBoost, ACL 2025, arXiv 2506.09289 (augTest.json; derived diff utboost_derived_changed_ids.txt): 36 (26 Verified) | 2025-07-22 | high |
| [SWE-bench maintainers / Meta FAIR (jacobkahn, jlko)](https://github.com/SWE-bench/SWE-bench/issues/465) | SWE-bench issue #465 Repo State Loopholes During Agentic Evaluation: 19 ids seen exploited in trajectories | 2025-09-03 .. 2026-03-24 | high |

## Counts

- 13 labelled positives, 0 controls; tiers: smoke 0, core 5, all 13.
- Categories: too_strict 4, underspecified 4, weak_tests 4, nop_passes 1.
- Most expected rules: RG408 (4), RG701 (4), RG101 (3), RG102 (3), RG110 (3), RG405 (2), RG006 (2), RG106 (2).
- Entries that only the deep review can find (`needs: deep-review`): 6.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-bench/<benchmark>.jsonl`; `--layout swebench`; needs the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-bench --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `openai-2024-annotations`: 1,699 rows; 650 us>=2, 1,039 fn>=2, 135 other, 1,160 filter_out - https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip
- `swe465-all-v1-images`: 500 + 1,794 - https://github.com/SWE-bench/SWE-bench/issues/465
