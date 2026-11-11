# swe-smith

SWE-smith synthetic bugs: harness defects reported in its issue tracker.

## Versions

- HF SWE-bench/SWE-smith, -py, -rs

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [SWE-bench/SWE-smith issue tracker](https://github.com/SWE-bench/SWE-smith/issues) | Issues #24 #245 #252 #254-#256: 3 named + row counts | 2025-05 .. 2026-11 | medium |

## Counts

- 3 labelled positives, 0 controls; tiers: smoke 0, core 3, all 3.
- Categories: forgeable_grader 1, gold_fails 1, nop_passes 1.
- Most expected rules: RG001 (2), RG207 (1), RG208 (1), RG110 (1), RG009 (1), RG002 (1), RG006 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-smith/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-smith --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `swesmith-252-bug-patch-tip`: ~11,500 rows - https://github.com/SWE-bench/SWE-smith/issues/252
