# mimo

MiMo-V2.6-RL-oss code tasks (Xiaomi). Vals AI reported two-thirds leaked the answer before the 2026-10-16 image re-push; no id list was published. Referenced as a bulk set.

## Versions

- HF XiaomiMiMo/MiMo-V2.6-RL-oss code config
- images xiaomimimo/mimo-v2.6-rl-oss:<instance_id>

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Vals AI](https://www.vals.ai/blogs/mimo-reward-hacking) | Two-Thirds of MiMo v2.6's Coding Tasks Leak the Answer: 1,795/2,698 leaky (no id list); names sglang-qwen-burst | 2026-10-07 | medium |

## Counts

- 0 labelled positives, 0 controls; tiers: smoke 0, core 0, all 0.
- Categories: none expanded (bulk only).
- Most expected rules: -.
- Entries that only the deep review can find (`needs: deep-review`): 0.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- `rlgate check <dir> --layout mimo`
- Fetch: `python benchmarks/fetch.py mimo --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `mimo-v2.6-code`: 2,602 tasks - https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss
