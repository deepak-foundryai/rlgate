# swe-bench-multilingual

SWE-bench Multilingual (300, 9 languages), human-labelled by PAIChecker and cross-checked against ABA.

## Versions

- HF SWE-bench/SWE-bench_Multilingual

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [CUHK-SZ (He lab)](https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data) | PAIChecker, ASE 2026, arXiv 2607.28587 (human labels): Verified 68/500, Multilingual 73/300, SWE-Gym 1,012/2,287 | 2026-07-30 | medium |
| [Wang, Zou et al.](https://arxiv.org/abs/2605.26079) | Automated Benchmark Auditing (Opus 4.7 labels per task): SWE-bench V 21, TB2 5, Pro 85, Multilingual 12 majors | 2026-05-18 (data) | low |

## Counts

- 11 labelled positives, 3 controls; tiers: smoke 0, core 5, all 14.
- Categories: too_strict 7, weak_tests 2, underspecified 2.
- Most expected rules: RG401 (7), RG403 (7), RG405 (7), RG408 (7), RG101 (2), RG105 (2), RG110 (2), RG704 (2).
- Entries that only the deep review can find (`needs: deep-review`): 2.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-bench-multilingual/<benchmark>.jsonl`; `rlgate check <out>/swe-bench-multilingual -j 8` (each row's eval script and log parser, graded as SWE-bench grades)
- Fetch: `python benchmarks/fetch.py swe-bench-multilingual --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `paichecker-multilingual`: 300 (73 misaligned) - upstream/paichecker_multilingual_human_labels.csv
- `aba-swe-bench-multilingual`: 12 majors / 300 - https://data.autobenchaudit.com/benchmarks/swe_bench_multilingual/benchmark.json

## Vendored files

- `upstream/paichecker_multilingual_human_labels.csv`: PAIChecker (MIT), labelled_data/ - human labels for SWE-bench Multilingual (https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data, sha256 `7ca3e2d5f8d9f835…`)
