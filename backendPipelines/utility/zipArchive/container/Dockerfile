# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Pinned by digest; the tag records which release the digest resolves to (3.11.17-slim-trixie).
# The same base image as the coordinate transform pipeline.
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8

# Apply the Debian security updates published since the base image was built.
RUN apt-get update && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY ./requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# The archive engine streams from S3 to S3 and never writes to disk.
COPY ./zip_archive_pipeline/ ./zip_archive_pipeline/

# Normalize read bits: COPY preserves the build host's umask.
RUN chmod -R a+rX ./zip_archive_pipeline

RUN useradd --create-home --uid 10001 --user-group archiver
USER archiver

ENTRYPOINT ["python3", "-m", "zip_archive_pipeline"]
