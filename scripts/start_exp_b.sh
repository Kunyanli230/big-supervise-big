#!/usr/bin/env bash
set -euo pipefail
source /home/swebench/.openrouter_env
cd /home/swebench/big-supervise-big
sg docker -c ".venv/bin/python scripts/run_batches_of_10.py --checkout /home/swebench/SWE-bench_Pro-os --exp-root /home/swebench/big-supervise-big-exp --snap-dir /home/swebench/snapshots --phase b --batch-size 10 >> /home/swebench/big-supervise-big-exp/runner_b.log 2>&1"
# Invoke a second pass to ensure rerun_context_limit_tasks executes even if the first pass started before the script update
exec sg docker -c ".venv/bin/python scripts/run_batches_of_10.py --checkout /home/swebench/SWE-bench_Pro-os --exp-root /home/swebench/big-supervise-big-exp --snap-dir /home/swebench/snapshots --phase b --batch-size 10 >> /home/swebench/big-supervise-big-exp/runner_b.log 2>&1"
