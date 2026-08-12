# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.11-slim

# Install system dependencies for spatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libproj-dev \
    proj-data \
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

# Set entry point
ENTRYPOINT ["python3", "-m", "coord_transform_pipeline"]
