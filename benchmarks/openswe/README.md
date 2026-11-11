# openswe

OpenSWE (GAIR, gated): eval scripts that paste the gold fix before running tests.

## Versions

- HF GAIR/OpenSWE (gated)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [ritvik-sarvam (HF discussion #4)](https://huggingface.co/datasets/GAIR/OpenSWE/discussions/4) | OpenSWE eval scripts paste the gold fix before running tests: 2 named | 2026-05-28 | medium |

## Counts

- 2 labelled positives, 0 controls; tiers: smoke 0, core 2, all 2.
- Categories: nop_passes 2.
- Most expected rules: RG002 (2), RG006 (2), RG306 (2).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/openswe/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py openswe --tier core --dry-run`
- rlgate can run it: partial.
