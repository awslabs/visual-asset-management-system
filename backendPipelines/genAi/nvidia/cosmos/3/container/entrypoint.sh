#!/bin/bash
set -e

cd /opt/cosmos-framework

# HF transport hardening (observed xet client deadlocks mid-download in the
# predict pipeline). Fall back to the standard HF transport.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale download artifacts from prior crashed runs.
HF_CACHE=${HF_HOME:-/mnt/efs/cosmos-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
