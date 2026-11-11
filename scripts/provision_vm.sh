#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates tmux python3 python3-venv python3-pip docker.io

sudo systemctl enable --now docker
sudo usermod -aG docker swebench || true

mkdir -p /home/swebench/big-supervise-big-exp /home/swebench/snapshots

if [ ! -d /home/swebench/big-supervise-big/.git ]; then
    git clone https://github.com/Kunyanli230/big-supervise-big.git /home/swebench/big-supervise-big
fi

cd /home/swebench/big-supervise-big
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

if [ ! -d /home/swebench/SWE-bench_Pro-os ]; then
    .venv/bin/python -m big_supervise_big fetch-v2 --dest /home/swebench/SWE-bench_Pro-os
fi

echo "VM_PROVISION_COMPLETE"
