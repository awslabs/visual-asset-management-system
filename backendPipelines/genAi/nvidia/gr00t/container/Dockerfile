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
# Normalize read/traverse bits: COPY preserves the build host's umask, so on a hardened host
# (umask 077/027) these files land root-owned 600/640 and the non-root USER below cannot read them
# (PermissionError at import). a+rX = world-read files + traverse dirs, no plain-file exec bit.
RUN chmod -R a+rX /opt/ml/code

# Copy finetune training script to expected location
RUN mkdir -p /workspace/scripts
COPY finetune_gr00t.py /workspace/scripts/finetune_gr00t.py
# Normalize read bits for the non-root USER (umask-proof; see /opt/ml/code note above).
RUN chmod -R a+rX /workspace/scripts

WORKDIR /opt/ml/code

# Create the non-root runtime user (issue #327) and give it a real HOME plus ownership of every
# tree it writes at run time: /opt/ml/code (cwd), /workspace (PYTHONPATH, gr00t package + scripts),
# and $HOME. GR00T's EFS model cache is NOT fronted by an access point (its file system lives in
# gr00tCommon-construct.ts, untouched here); the launch-template userdata mounts the EFS root and
# `chown -R 10000:10000` the mount dir after mounting, so ownership is handled there, not by an AP.
# Point HF/Triton/uv caches at home-owned dirs (HF_HOME is redirected to EFS at runtime by the
# entrypoint; these are the fallbacks) and pre-create them.
RUN groupadd -g 10000 vams && \
    useradd -u 10000 -g 10000 -m -d /home/vams -s /bin/bash vams
ENV HOME=/home/vams \
    HF_HOME=/home/vams/.cache/huggingface \
    UV_CACHE_DIR=/home/vams/.cache/uv \
    TRITON_CACHE_DIR=/home/vams/.triton
RUN mkdir -p /home/vams/.cache/huggingface /home/vams/.cache/uv /home/vams/.triton && \
    chown -R vams:vams /home/vams /opt/ml/code /workspace

# Run as the non-root vams user (issue #327), switched only after the last root-owned build step.
# The EFS model cache mount point is owned by this uid/gid via the launch-template userdata chown
# after mount, so the container can read/write cached model weights without root.
# Numeric form so runtime runAsNonRoot checks can verify it without reading the image's /etc/passwd.
USER 10000:10000

ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
