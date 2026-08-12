# VAMS Cosmos 3 (omni) Pipeline Container
# Built on the NVIDIA Cosmos framework (https://github.com/NVIDIA/cosmos-framework)

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
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Install uv package manager (cosmos-framework requires uv >= 0.11.3)
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /usr/local/bin/

# Clone the NVIDIA Cosmos framework (serves all Cosmos 3 variants)
RUN git clone https://github.com/NVIDIA/cosmos-framework.git /opt/cosmos-framework

# Install Python dependencies using uv. The extras carry the runtime libraries
# the inference entrypoint imports (iopath, fvcore, and friends); the
# `cu128-train` group pins the CUDA 12.8 torch stack (torch, flash-attn, natten,
# transformer-engine) from the NVIDIA cosmos and PyTorch indexes.
WORKDIR /opt/cosmos-framework
RUN uv sync --all-extras --group cu128-train

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Add the venv bin to PATH so python/torchrun are available without activation
ENV PATH="/opt/cosmos-framework/.venv/bin:$PATH"

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
