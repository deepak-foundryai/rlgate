# terminal-bench-1

Terminal-Bench 1.0 tasks named in its issue tracker (legacy task.yaml format).

## Versions

- terminal-bench-1 (refs per entry; several 'main' to be pinned at fetch)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [harbor-framework/terminal-bench-1 issue tracker](https://github.com/harbor-framework/terminal-bench-1/issues) | Individual issues against TB 1.0 tasks: ~15 | 2025 | medium |

## Counts

- 15 labelled positives, 0 controls; tiers: smoke 0, core 3, all 15.
- Categories: answer_leak 3, weak_tests 3, gold_fails 3, impossible 2, flaky 2, forgeable_grader 1, broken_env 1.
- Most expected rules: RG001 (7), RG601 (3), RG306 (3), RG212 (3), RG110 (3), RG003 (2), RG213 (1), RG104 (1).
- Entries that only the deep review can find (`needs: deep-review`): 2.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- legacy TB1 format; use the Terminal Wrench `original_task/` copy where one exists
- Fetch: `python benchmarks/fetch.py terminal-bench-1 --tier core --dry-run`
- rlgate can run it: partial.
