#!/bin/bash
set -e

# Use CUDA forward-compat libraries to bridge host driver/container CUDA version gaps.
# The compat libcuda.so symlink and ldconfig entry are set up in the Dockerfile.
# At runtime, prepend to LD_LIBRARY_PATH so the compat libs take precedence.
COMPAT_DIR=$(find /usr/local/cuda*/compat -maxdepth 0 2>/dev/null | head -1)
if [ -n "$COMPAT_DIR" ] && [ -d "$COMPAT_DIR" ]; then
    export LD_LIBRARY_PATH="${COMPAT_DIR}:${LD_LIBRARY_PATH}"
fi

# Re-run ldconfig at runtime to pick up host-mounted NVIDIA driver libraries
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

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
