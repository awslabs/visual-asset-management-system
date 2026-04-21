# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Dockerfile for the Preview 3D Thumbnail pipeline container.
# Generates animated GIF or static image previews from 3D files
# using CPU-only headless rendering (OSMesa/Xvfb + PyVista/VTK).

FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim

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

# Start Xvfb for headless rendering, then run the pipeline.
# This replaces pv.start_xvfb() which is deprecated in newer PyVista versions.
ENTRYPOINT ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 &>/dev/null & exec python3 -m preview_pipeline \"$@\"", "--"]
