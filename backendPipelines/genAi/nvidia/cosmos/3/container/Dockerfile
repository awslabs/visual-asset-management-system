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

# Non-root runtime user (issue #327), created up front so HOME is fixed BEFORE the uv steps below.
# uv resolves its download cache (~/.cache/uv) and managed CPython install (~/.local/share/uv/python)
# from $HOME; the venv's `python` is a symlink into the latter. If those were created under /root
# (mode 0700), the runtime user could neither execute the interpreter nor re-run `uv sync`.
# Building with HOME=/home/vams puts them where uid 10000 can reach them; ownership is fixed up
# after `uv sync` further down. Build steps themselves stay root: /opt is root-owned, so cloning
# into /opt/cosmos-framework and creating /opt/ml/code as uid 10000 would fail with EACCES.
RUN groupadd -g 10000 vams && \
    useradd -u 10000 -g 10000 -m -d /home/vams -s /bin/bash vams
ENV HOME=/home/vams

# Initialize git-lfs (writes $HOME/.gitconfig, i.e. /home/vams/.gitconfig, so the runtime user sees it)
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
# chown in the SAME layer as the sync: a later `RUN chown -R` would copy the multi-GB venv into a
# second layer just to change ownership. Also pre-create the cache dirs the runtime user may write to
# (uv cache; HF/torch/pip fallbacks when HF_HOME is not pointed at EFS). inference.py runs
# python/torchrun with cwd=/opt/cosmos-framework, so the tree must be writable for __pycache__.
RUN uv sync --all-extras --group cu128-train && \
    mkdir -p /home/vams/.cache/uv /home/vams/.cache/huggingface /home/vams/.cache/torch \
             /home/vams/.cache/pip /home/vams/.local/share/uv && \
    chown -R vams:vams /home/vams /opt/cosmos-framework

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code && chown vams:vams /opt/ml/code
COPY --chown=vams:vams __main__.py model_manager.py inference.py manifest_io.py entrypoint.sh /opt/ml/code/
# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /opt/ml/code/entrypoint.sh && chmod +x /opt/ml/code/entrypoint.sh
# Normalize read/traverse bits: --chown makes vams the owner, but COPY still preserves the build
# host's umask, so on a hardened host (umask 077/027) the files are 600/640 and only owner-read —
# fragile for the non-root USER. a+rX = world-read files + traverse dirs, no plain-file exec bit.
RUN chmod -R a+rX /opt/ml/code

# Add the venv bin to PATH so python/torchrun are available without activation
ENV PATH="/opt/cosmos-framework/.venv/bin:$PATH"

WORKDIR /opt/ml/code

# Run as the non-root vams user (issue #327). The EFS model cache is owned by this uid/gid via
# userdata chown after mount, so the container can read/write cached model weights without root.
# Numeric form so runtime runAsNonRoot checks can verify it without reading the image's /etc/passwd.
USER 10000:10000

ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
