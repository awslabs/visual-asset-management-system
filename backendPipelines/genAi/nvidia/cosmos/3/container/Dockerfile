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
# The installer URL carries the version, so a rebuild installs the CLI this image was built and
# validated with rather than whatever is current. Override with --build-arg to move it.
ARG AWS_CLI_VERSION=2.36.39
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Install uv package manager (cosmos-framework requires uv >= 0.11.3)
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /usr/local/bin/

# Clone the NVIDIA Cosmos framework (serves all Cosmos 3 variants), pinned to a commit.
# The upstream default branch advances daily, so a clone with no ref makes the framework version part
# of WHEN the image was built: two builds of the same VAMS commit carry different inference code, and
# a rebuild to pick up a VAMS change also silently takes months of upstream change with it. Override
# with --build-arg to move the pin; a full 40-character commit id is required, since the rev-parse
# check below rejects a branch or tag name (both of which move).
ARG COSMOS_FRAMEWORK_REPO=https://github.com/NVIDIA/cosmos-framework.git
ARG COSMOS_FRAMEWORK_COMMIT=6ea69664ab873320d026fcaebda3f9674332028e
RUN git clone ${COSMOS_FRAMEWORK_REPO} /opt/cosmos-framework && \
    cd /opt/cosmos-framework && \
    git checkout --detach ${COSMOS_FRAMEWORK_COMMIT} && \
    test "$(git rev-parse HEAD)" = "${COSMOS_FRAMEWORK_COMMIT}" && \
    git rev-parse HEAD > /opt/cosmos-framework/VAMS_UPSTREAM_COMMIT && \
    git log -1 --format="cosmos-framework %H %ai %s"

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
