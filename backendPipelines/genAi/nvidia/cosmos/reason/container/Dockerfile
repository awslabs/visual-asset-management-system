# VAMS Cosmos Reason 2 Pipeline Container
# Based on the official NVIDIA Cosmos-Reason2 repository
# https://github.com/nvidia-cosmos/cosmos-reason2

FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    git \
    git-lfs \
    redis-server \
    tree \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Initialize git-lfs
RUN git lfs install

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /uvx /usr/local/bin/

# Clone cosmos-reason2 repository
RUN git clone https://github.com/nvidia-cosmos/cosmos-reason2.git /opt/cosmos-reason2

# Install Python dependencies using uv
WORKDIR /opt/cosmos-reason2
RUN uv sync --locked --extra=cu128

# Triton bundled ptxas doesn't support latest GPU architectures
ENV TRITON_PTXAS_PATH="/usr/local/cuda/bin/ptxas"

# Triton JIT compilation needs Python.h at /usr/include/python3.12/.
# The uv-managed standalone Python does not include dev headers.
# Install the system python3-dev AFTER uv sync, and only for the headers.
# Note: This installs system python3.12-dev for headers only - the venv Python is still used at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends python3-dev && rm -rf /var/lib/apt/lists/*


# Fix for Triton JIT compilation of cuda_utils.c in containerized environments.
# 1. gcc needs an unversioned libcuda.so symlink — the compat dir only has .so.1
# 2. Patch Triton's build.py to NOT suppress stderr, so we can see gcc errors in CloudWatch
#
# The CUDA forward-compatibility libraries in /usr/local/cuda-12.8/compat are NOT registered with
# ldconfig. Forward compatibility only works when the host driver is older than the container's CUDA
# runtime; when the host driver is newer (the Amazon Linux 2023 NVIDIA AMI ships a CUDA 13 driver),
# a system-wide compat libcuda.so.1 shadows the host driver and CUDA initialization fails with
# error 803, "system has unsupported display driver / cuda driver combination". The compat path stays
# on disk so the Triton JIT link step below can still reference it explicitly for compilation.
# For the same reason, entrypoint.sh must not place the compat directory on LD_LIBRARY_PATH — that
# reintroduces the shadowing at run time even though ldconfig never registered it.
RUN ln -sf /usr/local/cuda-12.8/compat/libcuda.so.1 /usr/local/cuda-12.8/compat/libcuda.so && \
    TRITON_BUILD_PY=$(find /opt/cosmos-reason2/.venv -path "*/triton/runtime/build.py" | head -1) && \
    if [ -n "$TRITON_BUILD_PY" ]; then \
        sed -i 's/subprocess.check_call(cc_cmd, stdout=subprocess.DEVNULL)/subprocess.check_call(cc_cmd, stderr=subprocess.STDOUT)/' "$TRITON_BUILD_PY" && \
        echo "Patched Triton build.py: $TRITON_BUILD_PY"; \
    fi && \
    TRITON_DRIVER_PY=$(find /opt/cosmos-reason2/.venv -path "*/triton/backends/nvidia/driver.py" | head -1) && \
    if [ -n "$TRITON_DRIVER_PY" ]; then \
        sed -i 's|library_dirs=\[.*nvidia_lib_dir|library_dirs=["/usr/local/cuda-12.8/compat", nvidia_lib_dir|' "$TRITON_DRIVER_PY" 2>/dev/null || true && \
        echo "Attempted to patch Triton driver.py: $TRITON_DRIVER_PY"; \
    fi

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Add the venv bin to PATH so python/cosmos-reason2-inference are available without activation
ENV PATH="/opt/cosmos-reason2/.venv/bin:$PATH"

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
