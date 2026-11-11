# multi-swe-bench

Multi-SWE-bench (1,632): tasks reported broken in its issue tracker.

## Versions

- HF ByteDance-Seed/Multi-SWE-bench

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [multi-swe-bench/multi-swe-bench issue tracker](https://github.com/multi-swe-bench/multi-swe-bench/issues) | Issues #57 #70 #100 #105 #106: ~22 ids | 2025-08 .. 2026-09 | medium |

## Counts

- 9 labelled positives, 0 controls; tiers: smoke 0, core 4, all 9.
- Categories: gold_fails 4, underspecified 3, broken_env 1, too_strict 1.
- Most expected rules: RG001 (5), RG608 (4), RG704 (3), RG402 (2), RG006 (1), RG009 (1), RG701 (1), RG405 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/multi-swe-bench/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py multi-swe-bench --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `mswe-70-future-commits`: all - https://github.com/multi-swe-bench/multi-swe-bench/issues/70
