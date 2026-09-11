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
# The installer URL carries the version, so a rebuild installs the CLI this image was built and
# validated with rather than whatever is current. Override with --build-arg to move it.
ARG AWS_CLI_VERSION=2.36.39
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /uvx /usr/local/bin/

# Clone cosmos-predict2.5, pinned to a commit. A clone with no ref makes the framework version part of
# WHEN the image was built rather than of what VAMS commit built it, and two things here depend on the
# revision being fixed: `uv sync --locked` below resolves against the lockfile AT this commit, and
# entrypoint.sh patches checkpoint_db.py by matching an exact upstream source line, which silently
# no-ops when upstream edits that line. Override with --build-arg to move the pin; a full
# 40-character commit id is required, since the rev-parse check below rejects a branch or tag name
# (both of which move).
ARG COSMOS_PREDICT_REPO=https://github.com/nvidia-cosmos/cosmos-predict2.5.git
ARG COSMOS_PREDICT_COMMIT=a2c298b0a3df3778b973fe65e9e58877b292d8a7
RUN git clone ${COSMOS_PREDICT_REPO} /opt/cosmos-predict2.5 && \
    cd /opt/cosmos-predict2.5 && \
    git checkout --detach ${COSMOS_PREDICT_COMMIT} && \
    test "$(git rev-parse HEAD)" = "${COSMOS_PREDICT_COMMIT}" && \
    git rev-parse HEAD > /opt/cosmos-predict2.5/VAMS_UPSTREAM_COMMIT && \
    git log -1 --format="cosmos-predict2.5 %H %ai %s"

# Install Python dependencies using uv
WORKDIR /opt/cosmos-predict2.5
# Pin Python 3.10 because flash-attn==2.7.3+cu128.torch27 only ships a cp310 wheel.
# Upstream cosmos-predict2.5 switched its .python-version to 3.13; without this pin,
# uv picks CPython 3.13 and the flash-attn install fails with a platform/ABI error.
RUN uv python pin 3.10
RUN uv sync --locked --extra=cu128 --python 3.10

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Add the venv bin to PATH so python/torchrun are available without activation
ENV PATH="/opt/cosmos-predict2.5/.venv/bin:$PATH"

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
