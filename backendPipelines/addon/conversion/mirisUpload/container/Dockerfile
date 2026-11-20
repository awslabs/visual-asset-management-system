# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Pinned by digest; the tag records which release the digest resolves to (3.12.16-slim-trixie).
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6

# Apply the Debian security updates published since the base image was built, in one layer so the
# package lists never persist
RUN apt-get update && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /opt/miris-upload

# Install Python dependencies from the hash-locked set. requirements.txt lists the direct dependencies;
# requirements.lock is generated from it (command in its header) and pins every package, transitive ones
# included, by version and hash, so two builds of the same commit install the same packages.
COPY requirements.lock /build/requirements.lock
RUN chmod a+r /build/requirements.lock && \
    pip install --no-cache-dir --require-hashes -r /build/requirements.lock

# Normalize read bits after each COPY: COPY preserves the build host's umask, so a hardened checkout
# (umask 077/027) leaves these root-owned 600/640 and unreadable to the non-root USER below.
COPY __init__.py __main__.py dependency_manifest.py entrypoint.sh memory_budget.py miris_uploader.py preflight.py stream_metadata.py time_budget.py upload_config.py usd_packager.py ./
RUN chmod -R a+rX /opt/miris-upload && chmod a+x entrypoint.sh
COPY utils/ utils/
RUN chmod -R a+rX /opt/miris-upload/utils

# The runtime user, and the one path it writes: /workdir holds the downloaded asset and the
# packaged upload.
RUN useradd --create-home --uid 10001 --user-group mirisupload && \
    mkdir -p /workdir && \
    chown mirisupload:mirisupload /workdir

USER mirisupload

ENTRYPOINT ["./entrypoint.sh"]
