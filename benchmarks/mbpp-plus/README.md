# mbpp-plus

MBPP (original tests) tasks that EvalPlus lists as wrong, ambiguous or with multiple valid answers; MBPP+ is the fixed twin.

## Versions

- HF google-research-datasets/mbpp (full)
- MBPP+ v0.2.0 (twins)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [EvalPlus (UIUC)](https://github.com/evalplus/evalplus/blob/mbpp/groundtruth/mbpp/README.md) | MBPP+ ground-truth issue list (mbpp branch groundtruth/mbpp/README.md); HumanEval+ paper: ~60 MBPP ids; HumanEval 18 (only #124 named) | 2023-11 .. | medium |

## Counts

- 10 labelled positives, 0 controls; tiers: smoke 0, core 5, all 10.
- Categories: too_strict 6, underspecified 3, flaky 1.
- Most expected rules: RG408 (6), RG001 (5), RG407 (3), RG701 (3), RG704 (2), RG003 (1).
- Entries that only the deep review can find (`needs: deep-review`): 4.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- fetch.py writes `task.rlgate.yaml` per task: `rlgate check <out>/mbpp-plus -j 8`
- Fetch: `python benchmarks/fetch.py mbpp-plus --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `mbpp-evalplus-list`: ~60 - https://github.com/evalplus/evalplus/blob/mbpp/groundtruth/mbpp/README.md
