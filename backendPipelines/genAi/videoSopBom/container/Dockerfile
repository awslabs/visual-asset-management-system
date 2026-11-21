# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Pinned by digest; the tag records which release the digest resolves to (3.12.x-slim-trixie). The digest
# is the multi-arch index, which is what keeps --platform=linux/amd64 resolving.
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# ffmpeg and ffprobe from the Debian archive. Debian builds ffmpeg with --enable-gpl, so the binaries
# are GPL-2+ (see /usr/share/doc/ffmpeg/copyright in the image and NOTICE.md). The digest pin fixes the
# Debian packages at the day the base was built, so the layer applies the security updates Debian has
# published since then before it installs, in one layer so the package lists never persist.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies: every package at the version and hash in the lock (generated from
# requirements.txt; its header holds the command that regenerates it), as wheels only.
COPY ./requirements.lock ./requirements.lock
RUN pip3 install --no-cache-dir --require-hashes --only-binary :all: -r requirements.lock

# Copy container pipeline application
COPY ./video_sop_bom_pipeline/ ./video_sop_bom_pipeline/
# Normalize read bits: COPY preserves the build host's umask, so a hardened checkout (umask 077/027)
# leaves these root-owned 600/640 and unreadable to the non-root USER below.
RUN chmod -R a+rX ./video_sop_bom_pipeline

# Drop root; the pipeline reads /app and writes only under /app/tmp and /tmp
RUN useradd --create-home --uid 10001 --user-group videosopbom \
    && mkdir -p /app/tmp \
    && chown -R videosopbom:videosopbom /app/tmp
ENV HOME=/home/videosopbom
USER videosopbom

# Exec form so the Python process is PID 1 and receives Batch's SIGTERM directly
ENTRYPOINT ["python3", "-m", "video_sop_bom_pipeline"]
