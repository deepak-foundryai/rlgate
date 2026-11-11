# r2e-gym

R2E-Gym Subset: rows Prime Intellect dropped because the gold patch fails.

## Versions

- HF PrimeIntellect/R2E-Gym-Subset-Verified (split dropped)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Prime Intellect](https://huggingface.co/collections/PrimeIntellect/swe-rl-6a4d7a3ada4336936bc08915) | -Verified SWE-RL re-uploads with per-row gold/no-op/no-edit validation: Multi-SWE 11 no-edit / 172 retest / 5 apply-fail; SWE-rebench-V2 634 flaky / 145 no-edit / 3 always-fail; R2E 56 drops | 2026-07-22 .. 2026-10-02 | high |

## Counts

- 1 labelled positives, 0 controls; tiers: smoke 0, core 1, all 1.
- Categories: gold_fails 1.
- Most expected rules: RG001 (1), RG003 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/r2e-gym/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py r2e-gym --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `prime-r2e-drops`: 56 of 4,578 - https://huggingface.co/datasets/PrimeIntellect/R2E-Gym-Subset-Verified
