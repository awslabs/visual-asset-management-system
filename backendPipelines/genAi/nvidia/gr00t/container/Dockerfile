# VAMS Gr00t Fine-Tuning Pipeline Container
# Based on the NVIDIA Isaac-GR00T sample Dockerfile
# https://github.com/NVIDIA/Isaac-GR00T

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

ARG USE_STABLE=true
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
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
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
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1
ENV PYTHONPATH=/workspace

# Clone Isaac-GR00T repository
RUN git clone https://github.com/NVIDIA/Isaac-GR00T.git /workspace && \
    cd /workspace && \
    if [ "${USE_STABLE}" = "true" ]; then \
        echo "Using stable commit: ${STABLE_COMMIT}"; \
        git checkout ${STABLE_COMMIT}; \
    else \
        echo "Using latest version from main branch"; \
    fi && \
    echo "GR00T version info:" && \
    git log -1 --format="%H %ai %s"

WORKDIR /workspace

# Install dependencies
RUN uv pip install --upgrade pip setuptools wheel
RUN uv pip install --no-cache -e .[base]
RUN pip install --no-build-isolation flash-attn==2.7.1.post4
RUN uv pip install --no-cache gpustat

# Copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py inference.py model_manager.py entrypoint.sh /opt/ml/code/
RUN chmod +x /opt/ml/code/entrypoint.sh

# Copy finetune training script to expected location
RUN mkdir -p /workspace/scripts
COPY finetune_gr00t.py /workspace/scripts/finetune_gr00t.py

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
