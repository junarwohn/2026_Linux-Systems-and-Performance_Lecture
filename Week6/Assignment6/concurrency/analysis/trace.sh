#!/usr/bin/env bash
# Collect the privileged evidence when both tools are installed.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v bpftrace
command -v perf
mkdir -p logs
for q in 1 64; do
    sudo bpftrace tools/futex.bt -c "./bin/bqtest -p 2 -c 2 -n 20000 -q $q" > "logs/l1-futex-q$q.txt"
done
for mode in lock snapshot handoff; do
    bash tools/l2_run.sh "$mode"
done
TAG=lock-D5 bash tools/l2_run.sh lock -D 5
TAG=lock-cpu bash tools/l2_run.sh lock -D 0 -c 1
bash tools/burn.sh 1 30 &
TAG=lock-burn bash tools/l2_run.sh lock -D 0 -c 1
wait
bash tools/burn.sh 1 30 &
TAG=snapshot-burn bash tools/l2_run.sh snapshot -D 0 -c 1
wait
