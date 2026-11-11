#!/usr/bin/env bash
set -euo pipefail
while pgrep -f "run_batches_of_10.py" >/dev/null; do
    sleep 30
done
exec /home/swebench/big-supervise-big/scripts/start_exp_b.sh
