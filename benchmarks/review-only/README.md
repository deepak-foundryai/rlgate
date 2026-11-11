# Review-only benchmarks

Benchmarks with published task-level flaw labels that rlgate can't run yet: tool-calling simulators (tau-bench, tau2), desktop VMs (OSWorld), self-hosted web apps (WebArena), function-calling graders (BFCL), GPU kernels (KernelBench), and Q&A answer keys (HLE, HealthBench, CritPt, SimpleQA). They are kept here for an LLM-review-only comparison and for future layouts.

| Benchmark | Source | Confidence |
|---|---|---|
| tau2-bench | [Sierra](https://github.com/sierra-research/tau2-bench/blob/main/CHANGELOG.md) | high |
| tau2-bench-verified | [Amazon AGI](https://github.com/amazon-agi/tau2-bench-verified) | medium (high where Sierra also fixed the same task) |
| tau-bench (v1) | [ABC (UIUC Kang lab)](https://github.com/uiuc-kang-lab/agentic-benchmarks/tree/main/benchmarks/tau-bench) | high |
| osworld | [XLANG Lab](https://xlang.ai/blog/osworld-verified) | high |
| osworld-2.1 | [Epoch AI](https://epoch.ai/benchmarks/osworld-2-1/review) | medium |
| webarena | [ServiceNow](https://github.com/ServiceNow/webarena-verified) | medium |
| bfcl-v4 | [Tokenless](https://github.com/tokenless-ai/envcheck-findings/tree/post/2026-10-grader-defects) | high |
| kernelbench | [ABC (UIUC Kang lab)](https://github.com/uiuc-kang-lab/agentic-benchmarks/tree/main/benchmarks/kernel-bench) | high |
| swe-lancer | [ABC](https://github.com/uiuc-kang-lab/agentic-benchmarks/tree/main/benchmarks/swe-lancer) | high |
| swe-bench-multimodal | [Anthropic](https://www-cdn.anthropic.com/08ab9158070959f88f296514c21b7facce6f52bc.pdf) | medium |
| hle | [Epoch AI](https://epoch.ai/benchmarks/hle/review) | medium |
| healthbench-professional | [Epoch AI](https://epoch.ai/benchmarks/healthbench-professional/review) | medium |
| critpt-v1 | [Epoch AI](https://epoch.ai/benchmarks/critpt/review) | medium |
| cmt-benchmark-v1.0.0 | [Epoch AI](https://epoch.ai/benchmarks) | medium |
| simpleqa-verified | [Epoch AI](https://epoch.ai/benchmarks/simple-qa-verified/review) | medium |
| textquests | [Epoch AI](https://epoch.ai/benchmarks/textquests/review) | medium |
| agents-last-exam-v1.1 | [Epoch AI](https://epoch.ai/benchmarks/agents-last-exam/review) | medium |
| vending-bench-2 | [Epoch AI](https://epoch.ai/benchmarks/vending-bench-2/review) | low |
| intercode-ctf / cybench | [Docent / ABC / 'Every Model Cheats' arXiv 2607.21763](https://arxiv.org/abs/2607.21763) | low |
| metr-re-bench | [METR (second-hand reports)](https://metr.org/) | low |
| mimo-v2.6 general / cyber | [Vals AI / HF XiaomiMiMo/MiMo-V2.6-RL-oss](https://www.vals.ai/blogs/mimo-reward-hacking) | low |
| gaia | [HF discussions (second-hand)](https://huggingface.co/datasets/gaia-benchmark/GAIA/discussions) | low |

`labels.yaml` has the ids (copied from the source where short, otherwise a pointer to the source's own table). `upstream/tau2_bench_verified_FIXES.md` is copied from amazon-agi/tau2-bench-verified (MIT).
