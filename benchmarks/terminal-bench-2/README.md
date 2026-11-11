# terminal-bench-2

Terminal-Bench 2.0 (89 Harbor tasks). Positives are pinned to the Harbor registry commit; controls are the same task in Terminal-Bench 2.1, which fixed the flagged issue.

## Versions

- terminal-bench-2@69671fbaac6d (2.0 registry pin)
- terminal-bench-2-1@12b7565679ce (2.1, controls)

## Audits

| Source | What it labelled | Date | Confidence |
|---|---|---|---|
| [Terminal-Bench team (Laude/Stanford)](https://github.com/harbor-framework/terminal-bench-2/pull/53) | Terminal-Bench 2.1 changelog, PR #53 + paper arXiv 2611.08417 (Table 2/3): 28 TB2.0 tasks | 2026-03-11 / 2026-11-05 | high |
| [Z.ai (zai-org)](https://huggingface.co/datasets/zai-org/terminal-bench-2-verified) | Terminal-Bench 2.0 Verified (HF dataset change log): ~32 TB2 tasks | 2026-05-08 / 2026-08-18 | medium |
| [Fewshot / Stanford / CMU](https://github.com/few-sh/terminal-wrench) | Terminal Wrench (verifier hardening), arXiv 2610.15924: 331 hackable tasks (14 TB2) | 2026-04-18 (data) / 2026-10-11 (paper) | high |
| [harbor-framework/terminal-bench-2 issue tracker](https://github.com/harbor-framework/terminal-bench-2/issues) | Individual issues against TB 2.0 (incl. ishan-nerlekar #92-#103 with passing near-miss solve.sh): ~25 | 2026-04 .. 2026-11 | medium |
| [harbor-framework/terminal-bench-2-1 issue tracker](https://github.com/harbor-framework/terminal-bench-2-1/issues) | Issues against TB 2.1 tasks (also present at the 2.0 pin): ~22 | 2026 | medium |
| [Jonathan Gabor (independent)](https://jonathanpgabor.substack.com/p/every-benchmark-is-broken) | Every Benchmark is Broken (TB2 audit, Opus-rated + human notes): 88 rated, ~13 named | 2026-01-23 | medium |
| [harbor-framework/terminal-bench-1 issue tracker](https://github.com/harbor-framework/terminal-bench-1/issues) | Individual issues against TB 1.0 tasks: ~15 | 2025 | medium |
| [DebugML (UPenn)](https://debugml.github.io/cheating-agents) | Finding Widespread Cheating on Popular Agent Benchmarks: 2 TB2 tasks | 2026-04-10 | medium |
| [Terminal-Bench maintainers](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.0.0) | TB 4.0.0 release notes: tasks removed from 3.0 / modified: 8 removed + 19 modified | 2026-08-26 | high |
| [harbor-framework/terminal-bench maintainers + reporters](https://github.com/harbor-framework/terminal-bench/issues) | Issue tracker (563 issues, dump harbor_terminal-bench_issues_2026-11-11.tsv): 402 issues naming TB4 tasks | 2026-02-24 .. 2026-11-09 | medium |
| [Wang, Zou et al.](https://arxiv.org/abs/2605.26079) | Automated Benchmark Auditing (Opus 4.7 labels per task): SWE-bench V 21, TB2 5, Pro 85, Multilingual 12 majors | 2026-05-18 (data) | low |
| [UC Berkeley RDI](https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/) | How We Broke Top AI Agent Benchmarks (benchmark-wide exploits): benchmark-wide (TB 89, Verified 500, Pro 731) | 2026-04 | high |

## Counts

- 63 labelled positives, 19 controls; tiers: smoke 10, core 33, all 82.
- Categories: weak_tests 19, broken_env 11, underspecified 10, forgeable_grader 9, too_strict 7, answer_leak 4, gold_fails 2, flaky 1.
- Most expected rules: RG110 (23), RG608 (13), RG704 (11), RG001 (8), RG408 (8), RG215 (8), RG701 (7), RG109 (7).
- Entries that only the deep review can find (`needs: deep-review`): 21.

## How rlgate maps

Each entry's `expected_rules` lists the rlgate rules that should fire (see `docs/rules.md`). A control lists in `clean_for` the rules that must *not* fire on the fixed twin; other findings on a control are not scored.

## Running

- Harbor task dirs: `rlgate check <out>/terminal-bench-2 -j 8`
- Fetch: `python benchmarks/fetch.py terminal-bench-2 --tier core --dry-run`
- rlgate can run it: yes.

## Bulk label sets

Not expanded into `tasks:`; `labels.yaml` gives the upstream file and the filter rule.

- `gabor-tb2-ratings`: 88 rated - https://github.com/JonathanGabor/terminal-bench-2-audit
- `aba-tb2`: 5 majors / 89 - https://data.autobenchaudit.com/benchmarks/tb2/benchmark.json
- `rdi-all-forgeable`: 500 / 731 / 89 - https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/
- `zai-tb2-verified`: ~32 TB2 tasks changed - https://huggingface.co/datasets/zai-org/terminal-bench-2-verified
