#!/bin/bash
set -e

cd /workspace

# Ensure PYTHONPATH includes workspace for gr00t imports
export PYTHONPATH=/workspace:${PYTHONPATH}

# Preventive: disable hf_xet chunked transfer. The xet client has been
# observed to deadlock in futex_wait partway through multi-GB HF downloads
# while holding the HF cache lockfile, with no recovery. gr00t downloads
# the base model (e.g. nvidia/GR00T-N1.5-3B, ~6GB) via HuggingFace on
# first run. Falling back to standard HF transport is slower but reliable.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale xet/hf download artifacts from prior crashed runs.
HF_CACHE=${HF_HOME:-/mnt/efs/gr00t-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
