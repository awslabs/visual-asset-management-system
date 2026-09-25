# IsaacLab Training Container for VAMS
# Base: NVIDIA Isaac Lab pre-built container (headless)
ARG ISAAC_LAB_VERSION=2.3.0
ARG BASE_IMAGE=nvcr.io/nvidia/isaac-lab:${ISAAC_LAB_VERSION}

FROM ${BASE_IMAGE}

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

# Install additional dependencies using Isaac Sim's Python
COPY requirements.txt /tmp/requirements.txt
RUN /isaac-sim/kit/python/bin/python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

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
# Isaac Lab does runtime pip installs into Isaac Sim's site-packages (custom environment packages),
# Omniverse/Kit write caches under $HOME (~/.nvidia-omniverse, ~/.cache/ov, ~/.local/share/ov), and
# the entrypoint writes to /app and /tmp/assets. /mnt/efs/checkpoints is mounted via the EFS access
# point and already owned by 10000:10000, so it is not chowned here.
RUN groupadd -g 10000 vams && \
    useradd -u 10000 -g 10000 -m -d /home/vams -s /bin/bash vams && \
    chown -R vams:vams /app /tmp/assets /isaac-sim
ENV HOME=/home/vams \
    XDG_CACHE_HOME=/home/vams/.cache \
    HF_HOME=/home/vams/.cache/huggingface
RUN mkdir -p /home/vams/.cache /home/vams/.nvidia-omniverse /home/vams/.local/share && \
    chown -R vams:vams /home/vams

# Set entrypoint

# Run as non-root user. The EFS checkpoint storage is mounted via an EFS access point and owned
# by this uid/gid, so the container can read/write checkpoints.
USER 10000:10000

ENTRYPOINT ["/app/entrypoint.sh"]
