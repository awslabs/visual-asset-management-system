#!/bin/bash
set -e

# Activate conda environment
# Use explicit empty args to prevent $@ from leaking into activate
source ~/miniconda3/bin/activate cosmos-predict1

# Preventive: disable hf_xet chunked transfer (the xet client can deadlock
# in futex_wait partway through multi-GB checkpoint downloads while holding
# the HF cache lockfile). v1 uses huggingface_hub.snapshot_download which
# still uses the xet transport underneath.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale xet/hf download artifacts from prior crashed runs.
HF_CACHE=${HF_HOME:-/mnt/efs/cosmos-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

# Change to code directory
cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
