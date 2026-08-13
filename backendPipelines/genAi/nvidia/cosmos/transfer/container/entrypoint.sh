#!/bin/bash
set -e

# The CUDA forward-compatibility libraries in /usr/local/cuda*/compat are deliberately NOT placed on
# LD_LIBRARY_PATH. cosmos-transfer2.5 needs CUDA 12.8, which the AL2023 NVIDIA AMI's driver satisfies
# natively. Forward compatibility only applies when the host driver is OLDER than the container's CUDA
# version, and it is not backwards compatible: a compat libcuda.so.1 that precedes the host-mounted
# driver makes CUDA initialization fail with error 803, "system has unsupported display driver / cuda
# driver combination". The compat directory stays on disk for Triton's compile-time linking.

# Pick up the host-mounted NVIDIA driver libraries the container runtime injects.
ldconfig 2>/dev/null || true

# Ensure Python.h is findable for Triton JIT compilation.
if [ ! -f /usr/include/python3.10/Python.h ]; then
    PYTHON_INCLUDE=$(python -c "import sysconfig; print(sysconfig.get_path('include'))" 2>/dev/null)
    if [ -n "$PYTHON_INCLUDE" ] && [ -f "$PYTHON_INCLUDE/Python.h" ]; then
        mkdir -p /usr/include/python3.10
        ln -sf "$PYTHON_INCLUDE"/* /usr/include/python3.10/ 2>/dev/null || true
    fi
fi

# Log GPU diagnostics
echo "=== GPU Diagnostics ==="
nvidia-smi 2>&1 | head -5 || echo "nvidia-smi not available"
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH}"
python -c "import torch; print(f'torch.cuda.is_available()={torch.cuda.is_available()}, device_count={torch.cuda.device_count()}')" 2>&1 || echo "torch CUDA check failed"
echo "=== End GPU Diagnostics ==="

cd /opt/cosmos-transfer2.5

# Ensure deps are synced (fast if already done during build)
uv sync --locked --extra=${CUDA_NAME:-cu128} 2>/dev/null || true

# Disable hf_xet chunked transfer: the xet client has been observed to
# deadlock in futex_wait partway through multi-GB checkpoint downloads while
# holding the HF cache lockfile. Falling back to standard HF transport is
# slower but reliable.
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
# `uvx hf download ...` then `uvx hf download ... --quiet` wrapped in
# subprocess.check_output (capture_output=True). On a multi-GB download,
# progress output can fill the ~64KB OS pipe buffer and deadlock the child
# writing to stdout while the parent blocks in wait(). Patch once at startup
# to redirect the second call's stderr to DEVNULL so the pipe never fills.
CHECKPOINT_DB=/opt/cosmos-transfer2.5/cosmos_transfer2/_src/imaginaire/utils/checkpoint_db.py
if [ -f "$CHECKPOINT_DB" ] && ! grep -q "VAMS_PATCHED_CHECKOUTPUT" "$CHECKPOINT_DB"; then
    python -c "
import pathlib
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
