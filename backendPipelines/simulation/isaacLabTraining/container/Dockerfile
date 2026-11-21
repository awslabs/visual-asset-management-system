# IsaacLab Training Container for VAMS
# Base: NVIDIA Isaac Lab pre-built container (headless), pinned by digest: the tag records which release
# the digest resolves to, and a re-pointed tag cannot change what a rebuild of this commit pulls. Move the
# version and the digest together. The 3.0.0 image carries Isaac Sim 6.2, Python 3.12 in
# /opt/isaaclab-venv, and the Newton physics packages (issue #1669).
ARG ISAAC_LAB_VERSION=3.0.0
ARG ISAAC_LAB_DIGEST=sha256:f70acc9ed3bc3fc67099a0c5715379c432cc9965623cabbaf54f74599997e245
ARG BASE_IMAGE=nvcr.io/nvidia/isaac-lab:${ISAAC_LAB_VERSION}@${ISAAC_LAB_DIGEST}

# checkov:skip=CKV_DOCKER_7: BASE_IMAGE defaults to the digest-pinned isaac-lab:${ISAAC_LAB_VERSION} image; checkov does not resolve build args
FROM ${BASE_IMAGE}

# The base image runs as its own user (ubuntu, uid 1000); the build steps below need root. The
# final USER line drops to the VAMS runtime user.
USER root

# NVIDIA EULA acceptance - must be explicitly set to "Y" at build time
# See: https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license
ARG ACCEPT_EULA
RUN if [ "$ACCEPT_EULA" != "Y" ]; then \
      echo "ERROR: You must accept the NVIDIA EULA by setting ACCEPT_EULA=Y build arg"; \
      exit 1; \
    fi
ENV ACCEPT_EULA=${ACCEPT_EULA}
ENV PYTHONUNBUFFERED=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics

# The Isaac Lab Python environment. The interpreter is not on the base image's PATH (its entrypoint
# is runheadless.sh), so the pipeline names it here and puts the environment's bin first.
ENV ISAACLAB_VENV=/opt/isaaclab-venv
ENV VIRTUAL_ENV=/opt/isaaclab-venv
ENV PATH=/opt/isaaclab-venv/bin:${PATH}
# Recorded in the uploaded run configuration (isaacLabVersion) and printed at start. An ARG declared
# before FROM is not visible after it, so it is declared again.
ARG ISAAC_LAB_VERSION
ENV ISAAC_LAB_VERSION=${ISAAC_LAB_VERSION}

# Install additional dependencies into Isaac Lab's own environment: every package at the version and hash
# in the lock (generated from requirements.txt for Python 3.12; its header holds the command). The lock is
# resolved against the base image's frozen package set (base-constraints.txt), so it leaves every package
# the base ships at the base's version; regenerate base-constraints.txt whenever ISAAC_LAB_DIGEST moves,
# then the lock. boto3 is
# not in the base image; MoviePy 1.x with imageio-ffmpeg is what Isaac Lab's --video recording needs
# (MoviePy 2.x fails to import there). MoviePy 1.0.3 publishes no wheel, so it is the one package built
# from its source archive (--no-binary moviepy); its hash is still checked. --no-build-isolation builds it
# with the setuptools the base image ships (pinned in base-constraints.txt) instead of a build environment
# fetched from PyPI without hashes, so the build tool is fixed by the digest too.
COPY requirements.lock /tmp/requirements.lock
RUN /opt/isaaclab-venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary :all: --no-binary moviepy \
    --no-build-isolation -r /tmp/requirements.lock

# Create directories (issue #327: /mnt/efs/checkpoints is mounted via EFS access point, owned by 10000:10000)
RUN mkdir -p /app /tmp/assets

# Copy application code
WORKDIR /app
COPY . /app/

# Normalize read/traverse bits: COPY preserves the build host's umask, so on a hardened host
# (umask 077/027) these files land root-owned 600/640 and the non-root USER below cannot read them
# (PermissionError at import). a+rX = world-read files + traverse dirs, no plain-file exec bit.
# Runs before the chmod +x below so entrypoint.sh's exec bit is applied last.
RUN chmod -R a+rX /app

# Strip CRLF line endings from entrypoint.sh in case the source was bundled
# from a Windows working tree; otherwise the shebang becomes `/bin/bash\r`
# and the kernel returns ENOENT ("no such file or directory") on exec.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Create non-root user (issue #327) with a real HOME and ownership of paths the entrypoint writes.
# The user needs a passwd entry: torch (inductor) and others call getpass.getuser(), which raises
# KeyError for a uid with none. Omniverse/Kit write caches under $HOME (~/.nvidia-omniverse,
# ~/.cache/ov, ~/.local/share/ov), Warp and Newton compile and cache kernels (~/.cache/warp) and
# CUDA keeps its compute cache (~/.nv), and the entrypoint writes to /app and /tmp/assets.
# /mnt/efs/checkpoints is mounted via the EFS access point and already owned by 10000:10000, so it
# is not chowned here. The base image's Isaac Sim (/isaac-sim) is read, not written, except for the
# three Kit state folders given to the user in the next step.
RUN groupadd -g 10000 vams && \
    useradd -u 10000 -g 10000 -m -d /home/vams -s /bin/bash vams && \
    chown -R vams:vams /app /tmp/assets
ENV HOME=/home/vams \
    XDG_CACHE_HOME=/home/vams/.cache \
    HF_HOME=/home/vams/.cache/huggingface \
    WARP_CACHE_PATH=/home/vams/.cache/warp \
    CUDA_CACHE_PATH=/home/vams/.nv/ComputeCache
RUN mkdir -p /home/vams/.cache/warp /home/vams/.nvidia-omniverse /home/vams/.local/share \
        /home/vams/.nv/ComputeCache && \
    chown -R vams:vams /home/vams

# Kit writes its shader database, derived-data cache, generated OmniGraph node cache, user config
# and logs under /isaac-sim/kit/{cache,data,logs}. The PhysX evaluation path renders video with the
# RTX renderer inside Kit; when these folders are root-owned, the renderer cannot create its shader
# cache and fails with "HydraEngine rtx failed creating scene renderer", so the video comes out
# black (issue #1669, defect D1). Only these three folders are given to the runtime user; the rest
# of /isaac-sim stays as shipped.
RUN mkdir -p /isaac-sim/kit/cache /isaac-sim/kit/data /isaac-sim/kit/logs && \
    chown -R vams:vams /isaac-sim/kit/cache /isaac-sim/kit/data /isaac-sim/kit/logs

# A custom environment package is installed at run time with pip (download_and_install_custom_
# environment), which writes into the Isaac Lab environment: its site-packages is world-writable in
# the base image, and its bin folder (console scripts) gets the group and sticky model used for the working directory below, without
# a recursive change that would copy the environment into a new image layer.
RUN chgrp vams /opt/isaaclab-venv/bin && \
    chmod g+w,+t /opt/isaaclab-venv/bin

# Isaac Lab's train and play entry points write relative to their working directory,
# /workspace/isaaclab (__main__.py runs them there, as the base image's WORKDIR does), which the
# base image gives to its own user (uid 1000) with mode 0755 (issue #1312). Hydra creates its run
# folder outputs/<date>/<time> (with .hydra/ and the job log) for every run; training writes its run
# under logs/<library>/<experiment>/<time> (checkpoints, TensorBoard events, params, git diffs),
# which __main__.py uploads from as LOCAL_LOG_PATH and where it puts the evaluation policy; and a
# task can write files straight into the directory while its environment is built (the AutoMate
# tasks download their asset JSON and meshes to ./). logs and outputs belong to the user. The
# directory itself is group vams and sticky (1775, the /tmp model): the user can create entries in
# it but cannot rename or delete the entries the image ships (isaaclab.sh, scripts/, source/).
RUN mkdir -p /workspace/isaaclab/logs /workspace/isaaclab/outputs && \
    chown -R vams:vams /workspace/isaaclab/logs /workspace/isaaclab/outputs && \
    chgrp vams /workspace/isaaclab && \
    chmod 1775 /workspace/isaaclab

# Set entrypoint (the base image's own entrypoint is runheadless.sh, which is replaced here)

# Run as non-root user. The EFS checkpoint storage is mounted via an EFS access point and owned
# by this uid/gid, so the container can read/write checkpoints.
USER 10000:10000

ENTRYPOINT ["/app/entrypoint.sh"]
