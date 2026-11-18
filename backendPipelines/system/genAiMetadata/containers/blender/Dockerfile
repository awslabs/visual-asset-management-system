# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Lambda container image for the SYSTEM - GenAI Metadata pipeline's BLENDER render branch: Blender 5.2 LTS
# rendering 3D scenes headless on CPU (Cycles), plus trimesh for geometry attributes. Blender (GPL-3.0) is
# downloaded and verified at build time in the deploying account and invoked as a subprocess by handler.py;
# GPL3-license.txt travels beside renderScene.py, the only file that imports bpy.

# Pinned by digest, not by tag. The digest is the multi-arch manifest LIST for `lambda/python:3.12`, which is
# what keeps `--platform=linux/amd64` resolving; a per-platform digest would break any build on another
# architecture. To move it:
#   TOK=$(curl -s "https://public.ecr.aws/token/?scope=repository:lambda/python:pull" | jq -r .token)
#   curl -s -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
#        https://public.ecr.aws/v2/lambda/python/manifests/3.12 | sha256sum
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:32c247e8b227e6d1ef8b7f7a6d18dfa544b71b08f830d97d997fd68648d9d3fa

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Blender links X11, GL, and audio libraries and dlopens them even in --background mode; mesa-dri-drivers
# supplies the software GL that Cycles' display code probes at start-up. shadow-utils supplies useradd for the
# runtime user below (harmless when the base already carries it).
RUN dnf install -y \
    tar \
    xz \
    libGL \
    libGLU \
    libX11 \
    libXext \
    libXrender \
    libXi \
    libXxf86vm \
    libxkbcommon \
    libSM \
    libICE \
    libXfixes \
    libXcursor \
    libXrandr \
    libXinerama \
    mesa-libGL \
    mesa-dri-drivers \
    alsa-lib \
    pulseaudio-libs \
    shadow-utils \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Blender 5.2 LTS. Its bundled OpenUSD (26.03) is the library Blender's USD importer and renderScene.py's
# dependency check open the user's USD with; releases before 25.11 carry OpenUSD parser advisories
# (GHSA-58p5-r2f6-g2cj, GHSA-grjp-54v3-c442), which Blender 4.5 (25.02) and 5.0/5.1 (25.08) bundle. The
# checksum is the one Blender publishes in blender-<version>.sha256 beside the release; verifying it in the
# SAME layer as the download makes a tampered or truncated archive fail the build. The download base is an
# argument because download.blender.org is not reachable from every build network and its mirrors serve
# byte-identical archives.
ARG BLENDER_SERIES=5.2
ARG BLENDER_VERSION=5.2.3
ARG BLENDER_SHA256=78486c018b4443efe5e75ab54c290ed8c15abafd249b862c2c9ce2530a918eec
ARG BLENDER_DOWNLOAD_BASE=https://download.blender.org/release
RUN curl -fL "${BLENDER_DOWNLOAD_BASE}/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
        -o /tmp/blender.tar.xz \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && ln -s /opt/blender/blender /usr/local/bin/blender \
    && chmod -R a+rX /opt/blender
ENV BLENDER_VERSION=${BLENDER_VERSION}
ENV DISPLAY=
ENV XDG_RUNTIME_DIR=/tmp/runtime

# The handler's Python packages come from requirements.lock (generated from requirements.txt with
# `uv pip compile --generate-hashes`; the command is in its header), installed with --require-hashes so two
# builds of one commit ship the same code. usd-core gives the handler's own Python the OpenUSD reader the
# referenced-file walk uses; the build proves it imports.
COPY requirements.txt requirements.lock ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r ${LAMBDA_TASK_ROOT}/requirements.lock \
    && python -c "from pxr import Sdf, UsdUtils; print('usd-core ok')"

# Prove the installed Blender starts headless in this image and exposes everything renderScene.py uses: the
# importer operators (get_rna_type() raises KeyError for one that does not exist), bmesh for the mesh
# measurements, and the bundled USD Python module (pxr) that reads a stage's metersPerUnit, at an OpenUSD
# release that carries the 25.11 parser fixes (pxr.Usd.GetVersion() is (0, major, minor)). A renamed
# operator, a disabled add-on, a missing shared library or a Blender build without pxr fails here rather
# than on the first execution. Blender exits 0 after a Python exception in --python-expr unless
# --python-exit-code names another code, so the flag is what turns a failed probe into a failed build.
RUN mkdir -p /tmp/build-home && HOME=/tmp/build-home blender --background -noaudio --python-exit-code 1 --python-expr \
    "import bpy, bmesh; from pxr import Usd, UsdGeom; assert Usd.GetVersion() >= (0, 25, 11), Usd.GetVersion(); [getattr(bpy.ops.wm, n).get_rna_type() for n in ('obj_import', 'stl_import', 'ply_import', 'usd_import', 'alembic_import')]; bpy.ops.import_scene.fbx.get_rna_type(); bpy.ops.import_scene.gltf.get_rna_type(); print('importers ok', bpy.app.version_string, 'OpenUSD', Usd.GetVersion())" \
    && rm -rf /tmp/build-home

COPY handler.py meshAttributes.py formatUnits.py containerLogger.py executionLogKeys.py scene_dependencies.py renderScene.py GPL3-license.txt ${LAMBDA_TASK_ROOT}/

# Lambda runs the image under its own least-privileged default user regardless of USER, so every runtime
# file is made world-readable; the explicit non-root USER covers any other runtime of this image. Blender's
# writable directories (HOME, XDG_*, BLENDER_USER_RESOURCES) are placed under the invocation's /tmp work
# directory by handler.py, so no path outside /tmp is written at runtime. useradd is called by its absolute
# path: shadow-utils installs it under /usr/sbin, which the Lambda base image's PATH does not include.
RUN /usr/sbin/useradd --uid 10001 --user-group --no-create-home renderer \
    && chmod -R a+rX ${LAMBDA_TASK_ROOT}
ENV HOME=/tmp/home
USER renderer

CMD ["handler.lambda_handler"]
