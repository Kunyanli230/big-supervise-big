# Code and method provenance

Step supervision is adapted, at the user's direction, from:

- Kunyanli230/small-supervise-big-and-big-supervise-small, commit `7e8a2e3884e6c5d1417efd49adc8638c5f00dcd3`, `src/d3study/study.py`.
- The supervising `INSTRUCTIONS` string is retained verbatim; the sampling/selection/selected-history logic is re-expressed against hosted OpenRouter APIs.

The native agent and tool protocol are imported from mini-swe-agent 2.4.6 (MIT), https://github.com/SWE-agent/mini-swe-agent. Its sources and license remain in that dependency.

Benchmark task content and unchanged verifiers are fetched separately from ScaleAI/SWE-bench_Pro and scaleapi/SWE-bench_Pro-os at the recorded revision. They are not bundled into this code distribution. The benchmark's task content retains its upstream licenses.

This repository does not claim to reproduce training results or results from the archived source experiment.
