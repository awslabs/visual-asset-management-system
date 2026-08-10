#!/bin/bash
set -e

# The CUDA forward-compatibility libraries in /usr/local/cuda*/compat are deliberately NOT placed on
# LD_LIBRARY_PATH. Forward compatibility only applies when the host driver is OLDER than the
# container's CUDA version; the AL2023 NVIDIA AMI ships a newer driver, so a compat libcuda.so.1 that
# precedes the host-mounted driver makes CUDA initialization fail with error 803, "system has
# unsupported display driver / cuda driver combination". The compat directory stays on disk for
# Triton's compile-time linking (see the Dockerfile's library_dirs injection).

# Pick up the host-mounted NVIDIA driver libraries the container runtime injects.
ldconfig 2>/dev/null || true

# Ensure Python.h is findable for Triton JIT compilation.
# The uv-managed Python stores headers in the venv, not /usr/include/.
if [ ! -f /usr/include/python3.12/Python.h ]; then
    PYTHON_INCLUDE=$(python -c "import sysconfig; print(sysconfig.get_path('include'))" 2>/dev/null)
    if [ -n "$PYTHON_INCLUDE" ] && [ -f "$PYTHON_INCLUDE/Python.h" ]; then
        echo "Symlinking Python headers: $PYTHON_INCLUDE -> /usr/include/python3.12"
        ln -sf "$PYTHON_INCLUDE" /usr/include/python3.12
    fi
fi

# Log GPU diagnostics
echo "=== GPU Diagnostics ==="
echo "NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-not set}"
nvidia-smi 2>&1 | head -5 || echo "nvidia-smi not available"
python -c "import torch; print(f'torch.cuda.is_available()={torch.cuda.is_available()}, device_count={torch.cuda.device_count()}')" 2>&1 || echo "torch CUDA check failed"

# Pre-compile Triton CUDA utils to avoid JIT failures in EngineCore subprocess.
# The EngineCore subprocess fails to gcc-link cuda_utils.c because the compat libcuda.so
# isn't always findable. Pre-compiling here works because ldconfig was run above.
echo "Pre-compiling Triton CUDA utils..."
python -c "
try:
    from triton.backends.nvidia.driver import CudaUtils
    CudaUtils()
    print('Triton CUDA utils pre-compiled OK')
except Exception as e:
    print(f'Triton pre-compile failed: {e}')
" 2>&1

echo "=== End GPU Diagnostics ==="

cd /opt/cosmos-reason2

# Ensure deps are synced (fast if already done during build)
uv sync --locked --extra=${CUDA_NAME:-cu128} 2>/dev/null || true

# Preventive: disable hf_xet chunked transfer (the xet client can deadlock
# in futex_wait partway through multi-GB checkpoint downloads while holding
# the HF cache lockfile). Reason uses huggingface_hub's snapshot_download
# rather than cosmos's uvx hf path, but the xet transport is still used
# underneath and carries the same risk.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale xet/hf download artifacts from prior crashed runs.
HF_CACHE=${HF_HOME:-/mnt/efs/cosmos-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
