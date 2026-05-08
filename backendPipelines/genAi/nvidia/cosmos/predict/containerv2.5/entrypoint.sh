#!/bin/bash
set -e

cd /opt/cosmos-predict2.5

# Ensure deps are synced (fast if already done during build)
uv sync --locked --extra=${CUDA_NAME:-cu128} 2>/dev/null || true

# Disable hf_xet chunked transfer: we observed the xet client deadlocking in
# futex_wait partway through a ~5GB checkpoint download while holding the HF
# cache lockfile, with no recovery. Falling back to the standard HF transport
# is slower but reliable.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale xet/hf download artifacts from prior crashed runs so a fresh
# download can proceed without tripping on orphaned lockfiles or partial blobs.
HF_CACHE=${HF_HOME:-/mnt/efs/cosmos-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

# Cosmos's checkpoint_db._hf_download() does two back-to-back subprocess calls:
# `uvx hf download ...` then `uvx hf download ... --quiet` (with HF_HUB_OFFLINE=1)
# wrapped in subprocess.check_output (capture_output=True equivalent). On a
# multi-GB download, hf-cli progress output can fill the ~64KB OS pipe buffer
# and deadlock the child writing to stdout while the parent blocks in wait().
# Patch the function once at container start to pipe the second call's
# stdout/stderr to DEVNULL so the pipe never fills.
CHECKPOINT_DB=/opt/cosmos-predict2.5/cosmos_predict2/_src/imaginaire/utils/checkpoint_db.py
if [ -f "$CHECKPOINT_DB" ] && ! grep -q "VAMS_PATCHED_CHECKOUTPUT" "$CHECKPOINT_DB"; then
    python -c "
import re, pathlib
p = pathlib.Path('$CHECKPOINT_DB')
src = p.read_text()
old = 'return subprocess.check_output([*cmd, \"--quiet\"], text=True, env=dict(os.environ) | {\"HF_HUB_OFFLINE\": \"1\"}).strip()'
new = 'return subprocess.check_output([*cmd, \"--quiet\"], text=True, stderr=subprocess.DEVNULL, env=dict(os.environ) | {\"HF_HUB_OFFLINE\": \"1\"}).strip()  # VAMS_PATCHED_CHECKOUTPUT'
if old in src:
    p.write_text(src.replace(old, new))
    print('[VAMS] Patched checkpoint_db._hf_download')
else:
    print('[VAMS] checkpoint_db.py already patched or pattern changed — skipping')
"
fi

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
