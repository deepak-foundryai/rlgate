# swe-gym

SWE-Gym (2,438) with PAIChecker human labels and a malformed-test-id report.

## Versions

- HF SWE-Gym/SWE-Gym

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [SWE-Gym issue tracker](https://github.com/SWE-Gym/SWE-Gym/issues/24) | Issue #24 malformed FAIL_TO_PASS ids: 1 named | 2026-07-21 | medium |
| [CUHK-SZ (He lab)](https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data) | PAIChecker, ASE 2026, arXiv 2607.28587 (human labels): Verified 68/500, Multilingual 73/300, SWE-Gym 1,012/2,287 | 2026-07-30 | medium |

## Counts

- 9 labelled positives, 2 controls; tiers: smoke 0, core 5, all 11.
- Categories: too_strict 4, underspecified 3, broken_env 1, weak_tests 1.
- Most expected rules: RG408 (4), RG704 (3), RG701 (3), RG401 (2), RG403 (2), RG405 (2), RG006 (1), RG001 (1).
- Entries that only the deep review can find (`needs: deep-review`): 6.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-gym/<benchmark>.jsonl`; `--layout swebench`; needs the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-gym --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `paichecker-swe-gym`: 2,287 (1,012 misaligned) - upstream/paichecker_swe_gym_human_labels.csv

## Vendored files

- `upstream/paichecker_swe_gym_human_labels.csv`: PAIChecker (MIT), labelled_data/ - human labels for SWE-Gym (https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data, sha256 `1148c12ccc29c28d…`)
