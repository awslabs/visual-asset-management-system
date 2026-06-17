# VAMS Cosmos Predict 2.5 Pipeline Container
# Based on the official NVIDIA Cosmos-Predict2.5 Dockerfile pattern
# https://github.com/nvidia-cosmos/cosmos-predict2.5/blob/main/Dockerfile

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

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /uvx /usr/local/bin/

# Clone cosmos-predict2.5 repository
RUN git clone https://github.com/nvidia-cosmos/cosmos-predict2.5.git /opt/cosmos-predict2.5

# Install Python dependencies using uv
WORKDIR /opt/cosmos-predict2.5
# Pin Python 3.10 because flash-attn==2.7.3+cu128.torch27 only ships a cp310 wheel.
# Upstream cosmos-predict2.5 switched its .python-version to 3.13; without this pin,
# uv picks CPython 3.13 and the flash-attn install fails with a platform/ABI error.
RUN uv python pin 3.10
RUN uv sync --locked --extra=cu128 --python 3.10

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Add the venv bin to PATH so python/torchrun are available without activation
ENV PATH="/opt/cosmos-predict2.5/.venv/bin:$PATH"

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
