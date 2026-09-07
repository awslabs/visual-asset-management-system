# VAMS Gr00t Fine-Tuning Pipeline Container
# Based on the NVIDIA Isaac-GR00T sample Dockerfile
# https://github.com/NVIDIA/Isaac-GR00T

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

ARG STABLE_COMMIT=db107f03d165060998df166292578f1d7fb3c79a

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates unzip \
    git git-lfs \
    build-essential cmake \
    ffmpeg \
    libopencv-dev libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    python3.10 python3.10-dev python3.10-distutils python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
# The installer URL carries the version, so a rebuild installs the CLI this image was built and
# validated with rather than whatever is current. Override with --build-arg to move it.
ARG AWS_CLI_VERSION=2.36.39
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws && \
    rm -rf /usr/local/aws-cli/v2/*/dist/awscli/examples

# Initialize git-lfs
RUN git lfs install

# Set Python 3.10 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1

# Install UV package manager
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1
ENV PYTHONPATH=/workspace

# Clone Isaac-GR00T, pinned to a commit. STABLE_COMMIT is the revision this image's GPU runs were
# validated against; override with --build-arg to move the pin. A full 40-character commit id is
# required, since the rev-parse check below rejects a branch or tag name (both of which move).
ARG GR00T_REPO=https://github.com/NVIDIA/Isaac-GR00T.git
RUN git clone ${GR00T_REPO} /workspace && \
    cd /workspace && \
    git checkout --detach ${STABLE_COMMIT} && \
    test "$(git rev-parse HEAD)" = "${STABLE_COMMIT}" && \
    git rev-parse HEAD > /workspace/VAMS_UPSTREAM_COMMIT && \
    git log -1 --format="Isaac-GR00T %H %ai %s"

WORKDIR /workspace

# Install dependencies
RUN uv pip install --upgrade pip setuptools wheel
RUN uv pip install --no-cache -e .[base]
RUN pip install --no-build-isolation flash-attn==2.7.1.post4
RUN uv pip install --no-cache gpustat

# Copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py inference.py evaluation.py model_manager.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh

# Copy finetune training script to expected location
RUN mkdir -p /workspace/scripts
COPY finetune_gr00t.py /workspace/scripts/finetune_gr00t.py

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
