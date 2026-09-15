# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Pinned by digest; the tag records which release the digest resolves to (3.11.16-slim-trixie).
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.11-slim@sha256:d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0

# Install system dependencies for spatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libproj-dev \
    proj-data \
    # Sonames the open3d wheel links that the slim base does not carry:
    # libGL.so.1, libgomp.so.1, libX11.so.6 (libtbb and libc++ ship inside the wheel)
    libgl1 \
    libgomp1 \
    libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY ./requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy coord_xform library source
# (bundled into build context by CDK from coord-xform-pipeline/src/coord_xform/)
COPY ./coord_xform/ ./coord_xform/

# Copy container pipeline application
COPY ./coord_transform_pipeline/ ./coord_transform_pipeline/

# Drop root; the pipeline reads /app and writes only to a temporary directory under /tmp
RUN useradd --create-home --uid 10001 --user-group coordxform
USER coordxform

# Set entry point
ENTRYPOINT ["python3", "-m", "coord_transform_pipeline"]
