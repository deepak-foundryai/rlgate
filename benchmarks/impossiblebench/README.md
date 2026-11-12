# impossiblebench

ImpossibleBench: SWE-bench tasks whose tests were mutated to contradict the spec (`oneoff`, `conflicting`); the `original` row with the same instance id is the control. Labels are planted, so they are exact.

## Versions

- HF fjzzq2002/impossible_swebench

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Zhong, Raghunathan, Carlini (safety-research)](https://huggingface.co/datasets/fjzzq2002/impossible_swebench) | ImpossibleBench, arXiv 2510.20270: 349 x 3 splits (SWE), 103 x 3 (LCB) | 2025-10 | high |

## Counts

- 10 labelled positives, 4 controls; tiers: smoke 2, core 9, all 14.
- Categories: impossible 10.
- Most expected rules: RG001 (10), RG408 (10), RG704 (10).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/impossiblebench/<benchmark>.jsonl`; `rlgate check <out>/impossiblebench -j 8` (each row's eval script and log parser, graded as SWE-bench grades)
- Fetch: `python benchmarks/fetch.py impossiblebench --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `impossiblebench-swe-all`: 698 positives + 349 controls - https://huggingface.co/datasets/fjzzq2002/impossible_swebench
- `impossiblebench-lcb-all`: 206 positives + 103 controls; README 2026-10-12: pull latest harness (LCB test-edit bug) - https://huggingface.co/datasets/fjzzq2002/impossible_livecodebench
