# swe-rebench-v2

SWE-rebench-V2 (Nebius): images that ship a newer release with the fix (HF #7), plus Prime Intellect's per-row gold / no-op validation.

## Versions

- HF nebius/SWE-rebench-V2
- HF PrimeIntellect/SWE-rebench-V2-Filtered-Verified (validation files)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Fangjian Ju (HF discussion #7, acknowledged by Nebius)](https://huggingface.co/datasets/nebius/SWE-rebench-V2/discussions/7) | SWE-rebench-V2 images ship a newer release containing the fix: 14 short ids | 2026-11-05 | high |
| [Prime Intellect](https://huggingface.co/collections/PrimeIntellect/swe-rl-6a4d7a3ada4336936bc08915) | -Verified SWE-RL re-uploads with per-row gold/no-op/no-edit validation: Multi-SWE 11 no-edit / 172 retest / 5 apply-fail; SWE-rebench-V2 634 flaky / 145 no-edit / 3 always-fail; R2E 56 drops | 2026-07-22 .. 2026-10-02 | high |

## Counts

- 24 labelled positives, 0 controls; tiers: smoke 0, core 6, all 24.
- Categories: answer_leak 14, flaky 4, nop_passes 4, broken_env 2.
- Most expected rules: RG302 (14), RG305 (14), RG308 (14), RG006 (5), RG003 (4), RG002 (4), RG001 (2), RG608 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-rebench-v2/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-rebench-v2 --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `prime-swe-rebench-v2`: 634 flaky / 145 no-edit / 49 apply-fail / 3 always-fail - https://huggingface.co/datasets/PrimeIntellect/SWE-rebench-V2-Filtered-Verified
