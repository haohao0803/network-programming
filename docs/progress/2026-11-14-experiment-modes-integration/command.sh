#!/bin/bash
set -euo pipefail
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$PWD"
{
    git rev-parse HEAD
    git status --porcelain
    uname -a
    python3 --version
    sh scripts/with_local_tc.sh
    sha256sum scripts/run_experiments.py scripts/run_netem.py scripts/summarize_results.py build/rudp-send build/rudp-recv
} > results/pr6-integration/environment.log
python3 -c 'import hashlib; from pathlib import Path; Path("results/pr6-integration/input.bin").write_bytes(hashlib.shake_256(b"PR6 Linux integration smoke only").digest(65536))'
sh scripts/with_local_tc.sh python3 scripts/run_netem.py \
    --file results/pr6-integration/input.bin \
    --output results/pr6-integration/netem-smoke \
    --modes fixed adaptive --repeats 1 --timeout-ms 1000 --retries 2 \
    --max-seconds 90 --seed 20261114
python3 scripts/summarize_results.py results/pr6-integration/netem-smoke/*/runs/runs.jsonl \
    --output results/pr6-integration/netem-summary
