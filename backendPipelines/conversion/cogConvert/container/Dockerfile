# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Both stages start from one digest-pinned base (miniforge3 26.5.3-0). The GDAL stack is NOT solved at build
# time: conda-explicit.txt lists every package by URL and SHA-256, so two builds of one commit install the same
# bytes. Python packages come from requirements.lock (uv pip compile --generate-hashes) with --require-hashes.
ARG BASE_IMAGE=condaforge/miniforge3:26.5.3-0@sha256:66f4b7d5dd07883181ef5370f44b43857008a61e8f5b9bcc07f3f3ee027f7e63

FROM --platform=linux/amd64 ${BASE_IMAGE} AS build

# Apply the security updates published since the base image was built, in the layer that installs packages
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY ./conda-explicit.txt ./requirements.lock ./
# Exact packages, no solver. The environment sits at the path the runtime stage copies it to.
# The package manager only installs the lock; it is removed so the runtime does not ship it.
# Each conda package's license files are copied to /opt/cog/licenses before the package cache is cleaned.
RUN conda create --yes --prefix /opt/cog --file conda-explicit.txt \
    && /opt/cog/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock \
    && /opt/cog/bin/python -m pip uninstall -y pip \
    && mkdir -p /opt/cog/licenses \
    && for d in /opt/conda/pkgs/*/info/licenses; do \
         p="$(basename "$(dirname "$(dirname "$d")")")"; cp -r "$d" "/opt/cog/licenses/$p"; \
       done \
    && conda clean --all --yes

FROM --platform=linux/amd64 ${BASE_IMAGE}

RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/cog /opt/cog

ENV PATH="/opt/cog/bin:${PATH}" \
    GDAL_DATA="/opt/cog/share/gdal" \
    PROJ_DATA="/opt/cog/share/proj" \
    PROJ_NETWORK="OFF" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY ./cog_convert_pipeline/ ./cog_convert_pipeline/

# Normalize read bits: COPY preserves the build host's umask, so a hardened checkout leaves these root-owned
# 600/640 and unreadable to the non-root USER below.
RUN chmod -R a+rX ./cog_convert_pipeline

# Drop root. The pipeline reads /app and writes only to a temporary directory under /tmp.
RUN useradd --create-home --uid 10001 --user-group cogconvert
USER cogconvert

ENTRYPOINT ["/opt/cog/bin/python", "-m", "cog_convert_pipeline"]
