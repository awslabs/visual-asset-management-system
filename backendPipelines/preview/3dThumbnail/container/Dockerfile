# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Dockerfile for the Preview 3D Thumbnail pipeline container.
# Generates animated GIF or static image previews from 3D files
# using CPU-only headless rendering (OSMesa/Xvfb + PyVista/VTK).

# Pinned by digest, not by tag. `python:3.12-slim` is a moving reference: a rebuild months apart can
# produce a different image from identical sources, so an image that stops working cannot be told apart
# from a change in this repository. The tag is kept alongside the digest for readability -- Docker
# resolves the digest and ignores the tag. Debian packages are the exception: each stage's upgrade layer
# (below) takes what Debian has published on the build day, so two builds of one commit can differ in
# Debian patch level, and the build log's `apt-get upgrade` lines list what moved.
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
#
# Both stages pin the same digest; move them together. The digest resolves to 3.12.15-slim-trixie.
#
# The pin fixes the Debian packages at the day the digest was built, so each stage's package layer runs
# `apt-get upgrade -y` first: the security updates Debian has published since then go into the image that
# build produces. AWS Batch on AWS Fargate patches the host, not the packages inside this image. A deployed
# image takes later Debian updates only when this directory changes and the build runs on a cold cache:
# CDK (ecs.AssetImage.fromAsset) skips the build when the directory's asset hash is already in the
# bootstrap ECR repository, and Docker reuses the cached upgrade layer, whose instruction and pinned parent
# do not change and which comes before every COPY. To take new updates deliberately, move the digest, or
# rebuild with --no-cache after a change to this directory.

# ---------------------------------------------------------------------------------------------------
# Builder stage: compilers, the Python environment, Node.js and splat-transform. Only the installed
# results are copied into the runtime stage, so gcc/g++/cmake/make and xz never reach the image a job
# pulls.
# ---------------------------------------------------------------------------------------------------
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Apply the Debian security updates, then install the build tools for native extensions without a
# prebuilt wheel (e.g. pye57, Open3D) and xz to unpack the Node.js release tarball, in one layer so the
# package lists never persist.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    cmake \
    make \
    xz-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from the hash-locked set into a virtual environment the runtime stage
# copies whole. requirements.txt lists the direct dependencies; requirements.lock is generated from it
# (command in its header) and pins every package, transitive ones included, by version and hash, so two
# builds of the same commit install the same packages.
COPY requirements.lock /build/requirements.lock
RUN chmod a+r /build/requirements.lock && \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /build/requirements.lock && \
    chmod -R a+rX /opt/venv

# Node.js for the PlayCanvas splat-transform CLI, which converts .spz/.sog/.ksplat/.lcc/.compressed.ply
# Gaussian splats to PLY on the CPU. The package needs Node.js >= 22 and Debian trixie ships 20, so a
# pinned release tarball is used, verified against its published SHA-256 in the same RUN.
ARG NODE_VERSION=22.23.3
ARG NODE_SHA256=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
RUN python3 -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/node.tar.xz')" \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" && \
    echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/node && \
    tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 --no-same-owner && \
    rm -rf /tmp/node.tar.xz /opt/node/include /opt/node/share/doc /opt/node/share/man && \
    chmod -R a+rX /opt/node

# splat-transform at the exact version and dependency tree of the committed package-lock.json
# (npm ci fails when the two disagree). Install scripts are skipped: the CLI's only native dependency
# (webgpu, Dawn) ships prebuilt binaries and is used for GPU work this pipeline never requests; the
# other platforms' binaries are removed. npm, npx and corepack are then deleted from /opt/node: the
# pipeline runs only node, so the package manager and its own dependencies are not shipped.
COPY splat-transform/package.json splat-transform/package-lock.json /opt/splat-transform/
RUN chmod -R a+rX /opt/splat-transform && \
    cd /opt/splat-transform && \
    PATH=/opt/node/bin:$PATH npm ci --omit=dev --ignore-scripts --no-audit --no-fund && \
    rm -rf node_modules/webgpu/dist/darwin-universal node_modules/webgpu/dist/linux-arm64 \
           node_modules/webgpu/dist/win32-arm64 node_modules/webgpu/dist/win32-x64 && \
    /opt/node/bin/node node_modules/@playcanvas/splat-transform/bin/cli.mjs --version && \
    rm -rf /opt/node/lib/node_modules/npm /opt/node/lib/node_modules/corepack \
           /opt/node/bin/npm /opt/node/bin/npx /opt/node/bin/corepack && \
    chmod -R a+rX /opt/splat-transform

# ---------------------------------------------------------------------------------------------------
# Runtime stage: the same pinned base, rendering libraries only.
# ---------------------------------------------------------------------------------------------------
FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# python environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Headless rendering environment variables
ENV DISPLAY=:99
ENV PYVISTA_OFF_SCREEN=true
ENV PYVISTA_TRAME_SERVER_PROXY_PREFIX=""
ENV MESA_GL_VERSION_OVERRIDE=4.5
ENV VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN=1

# Apply the Debian security updates, then install the system libraries for headless 3D rendering
# (OSMesa + Xvfb) and OCC/cadquery, in one layer so the package lists never persist.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    libgl1-mesa-dev \
    libosmesa6-dev \
    libglu1-mesa-dev \
    mesa-utils \
    xvfb \
    libxrender1 \
    libxext6 \
    libsm6 \
    # OCC/cadquery STEP file support
    libglib2.0-0 \
    libxkbcommon0 \
    # OpenMP runtime (Open3D); the builder's gcc used to pull it in
    libgomp1 \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# The Python environment, Node.js and splat-transform from the builder. Their read bits were normalised
# in the builder, in the RUN that created them; COPY --from keeps those modes. A chmod -R here would
# rewrite every file into a second layer and double the image, so each COPY --from is followed by a
# check that fails the build if anything is unreadable to the non-root USER below.
COPY --from=builder /opt/venv /opt/venv
RUN test -z "$(find /opt/venv ! -perm -a+r -print -quit)"
COPY --from=builder /opt/node /opt/node
RUN test -z "$(find /opt/node ! -perm -a+r -print -quit)"
COPY --from=builder /opt/splat-transform /opt/splat-transform
RUN test -z "$(find /opt/splat-transform ! -perm -a+r -print -quit)"
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Copy pipeline code
COPY ./preview_pipeline /app/preview_pipeline
# Normalize read bits: COPY preserves the build host's umask, so a hardened checkout (umask 077/027)
# leaves these root-owned 600/640 and unreadable to the non-root USER below.
RUN chmod -R a+rX /app/preview_pipeline

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
