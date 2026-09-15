# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Dockerfile for the Preview 3D Thumbnail pipeline container.
# Generates animated GIF or static image previews from 3D files
# using CPU-only headless rendering (OSMesa/Xvfb + PyVista/VTK).

# Pinned by digest, not by tag. `python:3.12-slim` is a moving reference: a rebuild months apart can
# produce a different image from identical sources, so an image that stops working cannot be told apart
# from a change in this repository. The tag is kept alongside the digest for readability -- Docker
# resolves the digest and ignores the tag.
#
# The digest is the multi-arch INDEX (`application/vnd.oci.image.index.v1+json`), not a single
# platform's manifest, which is what keeps `--platform=linux/amd64` resolving. Pinning a per-platform
# digest here would break any build on another architecture.
#
# To move it: resolve the new digest and confirm it is an index carrying linux/amd64 BEFORE editing --
# a wrong digest fails the build rather than falling back.
#   TOK=$(curl -s "https://public.ecr.aws/token/?scope=repository:docker/library/python:pull&service=public.ecr.aws" | jq -r .token)
#   curl -sI -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.oci.image.index.v1+json" \
#        https://public.ecr.aws/v2/docker/library/python/manifests/3.12-slim | grep -i docker-content-digest
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# python environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Headless rendering environment variables
ENV DISPLAY=:99
ENV PYVISTA_OFF_SCREEN=true
ENV PYVISTA_TRAME_SERVER_PROXY_PREFIX=""
ENV MESA_GL_VERSION_OVERRIDE=4.5
ENV VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN=1

# Install system dependencies for headless 3D rendering (OSMesa + Xvfb)
# and build tools needed by some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-dev \
    libosmesa6-dev \
    libglu1-mesa-dev \
    mesa-utils \
    xvfb \
    libxrender1 \
    libxext6 \
    libsm6 \
    # Build tools for native extensions (e.g., pye57, Open3D)
    gcc \
    g++ \
    cmake \
    make \
    # OCC/cadquery STEP file support
    libglib2.0-0 \
    libxkbcommon0 \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy pipeline code
COPY ./preview_pipeline /app/preview_pipeline

# Create the runtime user and every path it has to write.
#
# core.py calls _create_dir(["tmp", "input"]), a RELATIVE path, so it resolves against WORKDIR /app,
# which is root-owned; the directory is pre-created and handed to the user. PyVista, VTK and matplotlib
# all write caches under HOME, and matplotlib additionally needs MPLCONFIGDIR or it falls back to a
# temporary directory and warns on every render. Xvfb creates /tmp/.X11-unix/X99 and /tmp/.X99-lock at
# container start, so that directory is pre-created with the sticky world-writable mode the X server
# expects. Every one of these is a runtime-only failure a container build cannot catch.
RUN useradd --create-home --uid 10001 --user-group appuser && \
    mkdir -p /app/tmp /home/appuser/.config/matplotlib /tmp/.X11-unix && \
    chown -R appuser:appuser /app/tmp /home/appuser && \
    chmod 1777 /tmp/.X11-unix
ENV HOME=/home/appuser
ENV MPLCONFIGDIR=/home/appuser/.config/matplotlib

USER appuser

# Start Xvfb for headless rendering, then run the pipeline.
# This replaces pv.start_xvfb() which is deprecated in newer PyVista versions.
ENTRYPOINT ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 &>/dev/null & exec python3 -m preview_pipeline \"$@\"", "--"]
