# terminal-wrench

Terminal Wrench: 331 tasks from TB2, terminal-bench-pro, SETA and others where a model got full reward with a hack. Entries here are the non-TB2 originals; TB2 ones are filed under terminal-bench-2.

## Versions

- terminal-wrench@d8a29613235a

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Fewshot / Stanford / CMU](https://github.com/few-sh/terminal-wrench) | Terminal Wrench (verifier hardening), arXiv 2610.15924: 331 hackable tasks (14 TB2) | 2026-04-18 (data) / 2026-10-11 (paper) | high |

## Counts

- 3 labelled positives, 0 controls; tiers: smoke 0, core 3, all 3.
- Categories: weak_tests 2, forgeable_grader 1.
- Most expected rules: RG110 (2), RG104 (1), RG109 (1), RG201 (1), RG203 (1), RG215 (1).
- Entries that only the deep review can find (`needs: deep-review`): 1.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- Harbor task dirs: `rlgate check <out>/terminal-wrench -j 8`
- Fetch: `python benchmarks/fetch.py terminal-wrench --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `terminal-wrench-all`: 331 tasks, 3,632 rewarded hack trajectories - https://github.com/few-sh/terminal-wrench/tree/d8a29613235a0ef56a8b70b3142626a533da28c2/tasks
