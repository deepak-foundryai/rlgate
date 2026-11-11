# humaneval-plus

HumanEval/124, whose canonical solution has a precedence bug the original tests miss (HumanEval+).

## Versions

- HF openai/openai_humaneval

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [EvalPlus (UIUC)](https://github.com/evalplus/evalplus/blob/mbpp/groundtruth/mbpp/README.md) | MBPP+ ground-truth issue list (mbpp branch groundtruth/mbpp/README.md); HumanEval+ paper: ~60 MBPP ids; HumanEval 18 (only #124 named) | 2023-11 .. | medium |

## Counts

- 1 labelled positives, 0 controls; tiers: smoke 0, core 1, all 1.
- Categories: weak_tests 1.
- Most expected rules: RG110 (1), RG103 (1).
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- fetch.py writes `task.rlgate.yaml`
- Fetch: `python benchmarks/fetch.py humaneval-plus --tier core --dry-run`
- rlgate can run it: yes.
