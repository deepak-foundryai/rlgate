# multi-swe-rl

Multi-SWE-RL (ByteDance): rows that pass with no edit or whose gold patch fails to apply, from Prime Intellect's validation.

## Versions

- HF ByteDance-Seed/Multi-SWE-RL data_20240601_20250331
- HF PrimeIntellect/Multi-SWE-RL-Verified

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Prime Intellect](https://huggingface.co/collections/PrimeIntellect/swe-rl-6a4d7a3ada4336936bc08915) | -Verified SWE-RL re-uploads with per-row gold/no-op/no-edit validation: Multi-SWE 11 no-edit / 172 retest / 5 apply-fail; SWE-rebench-V2 634 flaky / 145 no-edit / 3 always-fail; R2E 56 drops | 2026-07-22 .. 2026-10-02 | high |
| [multi-swe-bench/multi-swe-bench issue tracker](https://github.com/multi-swe-bench/multi-swe-bench/issues) | Issues #57 #70 #100 #105 #106: ~22 ids | 2025-08 .. 2026-09 | medium |

## Counts

- 14 labelled positives, 0 controls; tiers: smoke 1, core 6, all 14.
- Categories: nop_passes 11, gold_fails 2, flaky 1.
- Most expected rules: RG002 (11), RG006 (11), RG001 (6), RG009 (2), RG003 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/multi-swe-rl/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py multi-swe-rl --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `prime-multi-swe-rl`: 11 / 5 / 172 / 79 / 449 - https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified
- `mswe-70-future-commits`: all - https://github.com/multi-swe-bench/multi-swe-bench/issues/70
