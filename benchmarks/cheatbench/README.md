# cheatbench

CheatBench (CAIS) software-engineering split: 20 SWE-bench Pro tasks that are leaky by construction (git history and network on). Referenced as a bulk set.

## Versions

- github.com/centerforaisafety/cheatbench tasks/software_engineering/instances.json

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [CAIS (Hendrycks et al.)](https://github.com/centerforaisafety/cheatbench) | CheatBench, arXiv 2609.36308 (software_engineering: 20 SWE-bench Pro tasks, hackable by construction): 20 SWE tasks | 2026-09-28 | high |

## Counts

- 0 labelled positives, 0 controls; tiers: smoke 0, core 0, all 0.
- Categories: none expanded (bulk only).
- Most expected rules: -.
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- Harbor registry `scale-ai/swe-bench-pro`
- Fetch: `python benchmarks/fetch.py cheatbench --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `cheatbench-swe`: 20 - https://github.com/centerforaisafety/cheatbench
