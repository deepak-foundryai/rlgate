# swe-bench-pro

SWE-bench Pro v1 (731) and V2. V2 dropped 89 v1 tasks and rebuilt images without the fix commits; v1 tasks with a V2 twin use it as a control.

## Versions

- HF ScaleAI/SWE-bench_Pro config v1 (tag v1.0)
- scaleapi/SWE-bench_Pro-os v2/tasks (V2)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Jonathan Gabor (independent)](https://www.lesswrong.com/posts/nAMhbz5sfpcynjPP5/swe-bench-pro-is-even-worse) | SWE-Bench Pro is even worse (100 random v1 tasks, Claude-assisted + manual): 100 rated | 2026-02-24 | low |
| [Shanghai AI Lab / OpenCompass](https://huggingface.co/datasets/opencompass/SWEBench-Pro-Verified) | SWE-Bench Pro Verified, arXiv 2609.08149 (102 ids by category in HF README): 102 (22 misleading / 75 narrow / 3 broad / 2 other) | 2026-09-08 | medium |
| [Datacurve](https://deepswe.datacurve.ai/blog/deepswe) | DeepSWE blog: cleaner verifier judgments (SWE-bench Pro trials): 5 trials + flipt cluster | 2026-05-26 | low |
| [Poolside](https://poolside.ai/blog/through-the-looking-glass) | Through the looking glass of benchmark hacking: 5 | 2026-05-11 | medium |
| [scaleapi/SWE-bench_Pro-os issue tracker](https://github.com/scaleapi/SWE-bench_Pro-os/issues) | Community issues #45 #72 #85 #115 #120 #121: ~10 | 2026 | medium |
| [Scale AI / SEAL](https://github.com/scaleapi/SWE-bench_Pro-os) | SWE-bench Pro V2 README + GATE.md (v1->V2 dropped ids derived by diffing the two releases): 89 dropped (derived), 4 flaky named | 2026-09-22 | medium |
| [Wang, Zou et al.](https://arxiv.org/abs/2605.26079) | Automated Benchmark Auditing (Opus 4.7 labels per task): SWE-bench V 21, TB2 5, Pro 85, Multilingual 12 majors | 2026-05-18 (data) | low |
| [UC Berkeley RDI](https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/) | How We Broke Top AI Agent Benchmarks (benchmark-wide exploits): benchmark-wide (TB 89, Verified 500, Pro 731) | 2026-04 | high |

## Counts

- 21 labelled positives, 3 controls; tiers: smoke 1, core 15, all 24.
- Categories: too_strict 6, flaky 5, underspecified 4, weak_tests 2, answer_leak 2, broken_env 2.
- Most expected rules: RG408 (6), RG704 (5), RG003 (5), RG110 (4), RG001 (4), RG701 (3), RG405 (3), RG308 (3).
- Entries that only the deep review can find (`needs: deep-review`): 6.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- V2: Harbor dirs (`rlgate check`); v1: SWE-style rows in `<out>/swe-bench-pro/<benchmark>.jsonl`; needs a mapping YAML (`--layout file`) and the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-bench-pro --tier core --dry-run`
- rlgate can run it: partial.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `scale-pro-v2-dropped`: 89 (derived by diff, not published) - upstream/v1_to_v2_dropped_ids.txt
- `opencompass-pro-102`: 22 / 75 / 3 / 2 - https://huggingface.co/datasets/opencompass/SWEBench-Pro-Verified/blob/main/README.md
- `gabor-pro-100`: 100 (rating 2-5) - https://github.com/JonathanGabor/swe-bench-pro-audit
- `aba-swe-bench-pro`: 85 majors / 731 - https://data.autobenchaudit.com/benchmarks/swe_bench_pro/benchmark.json
- `rdi-all-forgeable`: 500 / 731 / 89 - https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/

## Vendored files

- `upstream/v1_to_v2_dropped_ids.txt`: derived: v1 instance ids absent from V2 (SWE-bench_Pro-os, MIT). Scale did not publish this list. (https://github.com/scaleapi/SWE-bench_Pro-os, sha256 `9222d07108ce3166…`)
