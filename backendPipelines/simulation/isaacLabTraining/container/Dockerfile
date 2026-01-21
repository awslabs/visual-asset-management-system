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

# Create directories
RUN mkdir -p /app /mnt/efs/checkpoints /tmp/assets

# Copy application code
WORKDIR /app
COPY . /app/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
