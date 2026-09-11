# VAMS Cosmos Transfer 2.5 Pipeline Container
# Based on the official NVIDIA Cosmos-Transfer2.5 pattern
# https://github.com/nvidia-cosmos/cosmos-transfer2.5

FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    git \
    git-lfs \
    tree \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Initialize git-lfs
RUN git lfs install

# Install AWS CLI v2
# The installer URL carries the version, so a rebuild installs the CLI this image was built and
# validated with rather than whatever is current. Override with --build-arg to move it.
ARG AWS_CLI_VERSION=2.36.39
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /uvx /usr/local/bin/

# Clone cosmos-transfer2.5, pinned to a commit. A clone with no ref makes the framework version
# part of WHEN the image was built rather than of what VAMS commit built it, and two things here
# depend on the revision being fixed: `uv sync --locked` below resolves against the lockfile AT
# this commit, and entrypoint.sh patches checkpoint_db.py by matching an exact upstream source
# line, which silently no-ops when upstream edits that line. Override with --build-arg to move the
# pin; a full 40-character commit id is required, since the rev-parse check below rejects a branch
# or tag name (both of which move).
ARG COSMOS_TRANSFER_REPO=https://github.com/nvidia-cosmos/cosmos-transfer2.5.git
ARG COSMOS_TRANSFER_COMMIT=2ff49d0717af02057ae79bc75c00fbff9da1b4e7
RUN git clone ${COSMOS_TRANSFER_REPO} /opt/cosmos-transfer2.5 && \
    cd /opt/cosmos-transfer2.5 && \
    git checkout --detach ${COSMOS_TRANSFER_COMMIT} && \
    test "$(git rev-parse HEAD)" = "${COSMOS_TRANSFER_COMMIT}" && \
    git rev-parse HEAD > /opt/cosmos-transfer2.5/VAMS_UPSTREAM_COMMIT && \
    git log -1 --format="cosmos-transfer2.5 %H %ai %s"

# Install Python dependencies using uv
WORKDIR /opt/cosmos-transfer2.5
# Pin Python 3.10 because flash-attn==2.7.3+cu128.torch27 only ships a cp310 wheel.
# Upstream cosmos-transfer2.5 previously provided a .python-version pin; if that pin
# is absent uv will default to the newest CPython (3.13+) and the flash-attn install fails.
RUN uv python pin 3.10
RUN uv sync --locked --extra=cu128 --python 3.10

# Upgrade NCCL to a CUDA 12.8 compatible version.
# The uv sync installs NCCL 2.26.2+cuda12.2 which is incompatible with CUDA 12.8 compat driver.
# This must match the CUDA version provided by the compat libraries.
RUN uv pip install --python .venv/bin/python "nvidia-nccl-cu12>=2.26"

# Install python3-dev for Python.h headers (needed by Triton JIT compilation of cuda_utils.c)
# Must be installed AFTER uv sync to avoid conflicts with uv's standalone Python
RUN apt-get update && apt-get install -y --no-install-recommends python3-dev && rm -rf /var/lib/apt/lists/*

# Fix for Triton JIT compilation in containerized environments:
# Create unversioned libcuda.so symlink (compat dir only has .so.1)
#
# The CUDA forward-compatibility libraries in /usr/local/cuda-12.8/compat are NOT registered with
# ldconfig. Forward compatibility only works when the host driver is older than the container's CUDA
# runtime; when the host driver is newer (the Amazon Linux 2023 NVIDIA AMI ships a CUDA 13 driver),
# a system-wide compat libcuda.so.1 shadows the host driver and CUDA initialization fails with
# error 803, "system has unsupported display driver / cuda driver combination". The compat path stays
# on disk so the Triton JIT link step can still reference it explicitly for compilation.
RUN ln -sf /usr/local/cuda-12.8/compat/libcuda.so.1 /usr/local/cuda-12.8/compat/libcuda.so

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Add the venv bin to PATH so python/torchrun are available without activation
ENV PATH="/opt/cosmos-transfer2.5/.venv/bin:$PATH"

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
