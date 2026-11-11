# bigcodebench

BigCodeBench tasks fixed in later releases (missing requirements, unstated plot titles, network tests). The pre-fix split is the positive.

## Versions

- HF bigcode/bigcodebench split v0.1.0_hf (positives)
- split v0.1.4 (twins)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [BigCode](https://github.com/bigcode-project/bigcodebench/releases) | BigCodeBench release notes v0.2.0.post3 / v0.2.4: 9 ids | 2024 | medium |

## Counts

- 7 labelled positives, 0 controls; tiers: smoke 0, core 4, all 7.
- Categories: broken_env 3, underspecified 2, flaky 1, too_strict 1.
- Most expected rules: RG001 (3), RG403 (3), RG608 (2), RG704 (2), RG401 (2), RG003 (1), RG307 (1), RG607 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- fetch.py writes `task.rlgate.yaml`
- Fetch: `python benchmarks/fetch.py bigcodebench --tier core --dry-run`
- rlgate can run it: yes.
