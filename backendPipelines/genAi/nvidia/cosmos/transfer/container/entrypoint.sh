#!/bin/bash
set -e

# Use CUDA forward-compat libraries to bridge host driver (550, CUDA 12.4) to
# container requirements (CUDA 12.8). This is required because cosmos-transfer2.5
# needs NVIDIA driver 570+ / CUDA 12.8, but ECS_AL2_NVIDIA AMI has driver 550.
COMPAT_DIR=$(find /usr/local/cuda*/compat -maxdepth 0 2>/dev/null | head -1)
if [ -n "$COMPAT_DIR" ] && [ -d "$COMPAT_DIR" ]; then
    export LD_LIBRARY_PATH="${COMPAT_DIR}:${LD_LIBRARY_PATH}"
fi

# Re-run ldconfig at runtime to pick up host-mounted NVIDIA driver libraries
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

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
