# swe-bench-verified

SWE-bench Verified (500). Labels: weak tests (UTBoost, PAIChecker), spec/test mismatch (PAIChecker, OpenAI 2024, ABA), git-history leaks in the pre-Sept-2025 `:v1` images (#465). Ten instances that three independent audits call clean are controls.

## Versions

- HF SWE-bench/SWE-bench_Verified
- images swebench/sweb.eval.x86_64.*:v1 (leaky) and :v2 (fixed)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [SWE-bench maintainers / Meta FAIR (jacobkahn, jlko)](https://github.com/SWE-bench/SWE-bench/issues/465) | SWE-bench issue #465 Repo State Loopholes During Agentic Evaluation: 19 ids seen exploited in trajectories | 2025-09-03 .. 2026-03-24 | high |
| [CUHK-SZ / UIUC (Kang lab)](https://github.com/CUHK-Shenzhen-SE/UTBoost/blob/main/assets/useful_scripts/augTest.json) | UTBoost, ACL 2025, arXiv 2506.09289 (augTest.json; derived diff utboost_derived_changed_ids.txt): 36 (26 Verified) | 2025-07-22 | high |
| [CUHK-SZ (He lab)](https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data) | PAIChecker, ASE 2026, arXiv 2607.28587 (human labels): Verified 68/500, Multilingual 73/300, SWE-Gym 1,012/2,287 | 2026-07-30 | medium |
| [OpenAI (second-hand: openai.com 403)](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) | Why we no longer evaluate SWE-bench Verified; ids via greynewell: 138 audited, ids not public (2 cited second-hand) | 2026-02-23 | low |
| [OpenAI Preparedness + SWE-bench authors](https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip) | SWE-bench Verified human annotations (3 annotators, ensembled): 1,699 annotated (1,160 filter_out) | 2024-08-12 | medium |
| [Wang, Zou et al.](https://arxiv.org/abs/2605.26079) | Automated Benchmark Auditing (Opus 4.7 labels per task): SWE-bench V 21, TB2 5, Pro 85, Multilingual 12 majors | 2026-05-18 (data) | low |
| [UC Berkeley RDI](https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/) | How We Broke Top AI Agent Benchmarks (benchmark-wide exploits): benchmark-wide (TB 89, Verified 500, Pro 731) | 2026-04 | high |

## Counts

- 56 labelled positives, 13 controls; tiers: smoke 5, core 23, all 69.
- Categories: weak_tests 29, answer_leak 19, underspecified 6, too_strict 2.
- Most expected rules: RG110 (29), RG101 (26), RG102 (26), RG103 (26), RG301 (19), RG308 (19), RG704 (6), RG408 (3).
- Entries that only the deep review can find (`needs: deep-review`): 10.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- SWE-style rows in `<out>/swe-bench-verified/<benchmark>.jsonl`; `--layout swebench`; needs the repo checked out per row
- Fetch: `python benchmarks/fetch.py swe-bench-verified --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `openai-2024-annotations`: 1,699 rows; 650 us>=2, 1,039 fn>=2, 135 other, 1,160 filter_out - https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip
- `paichecker-verified`: 500 (68 misaligned) - upstream/paichecker_verified_labels.csv
- `utboost`: 26 Verified + 24 Lite (36 unique) - upstream/utboost_changed_ids.txt
- `swe465-all-v1-images`: 500 + 1,794 - https://github.com/SWE-bench/SWE-bench/issues/465
- `aba-swe-bench-verified`: 21 majors / 500 - https://data.autobenchaudit.com/benchmarks/swe_bench_verified/benchmark.json
- `rdi-all-forgeable`: 500 / 731 / 89 - https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/

## Vendored files

- `upstream/paichecker_verified_labels.csv`: PAIChecker (MIT), labelled_data/ - instance_id + label columns only (the upstream file is 6.8 MB with model outputs) (https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data, sha256 `9eff20c2399e4b57…`)
- `upstream/utboost_changed_ids.txt`: derived: instances whose tests UTBoost augmented, by diffing uiuc-kang-lab/SWE-bench-{Verified,Lite}-UTBoost against the originals (UTBoost code: MIT). Not a list the authors published. (https://github.com/CUHK-Shenzhen-SE/UTBoost, sha256 `b1ded300f056cbd0…`)
