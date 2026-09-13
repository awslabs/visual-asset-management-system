# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Lambda image for the MEDIA branch of the SYSTEM GenAI metadata pipeline: images, SVG, video, audio, PDF,
# text, tabular data and 3D Tiles tilesets. Video facts and keyframes come from the static ffmpeg binary the
# imageio-ffmpeg wheel bundles; the wheel ships no ffprobe, so stream facts are parsed from ffmpeg's own
# header output.
#
# Pinned by digest, not by tag. `lambda/python:3.12` is a moving reference: AWS republishes it with runtime
# patches, so a rebuild months apart can produce a different image from identical sources. The tag is kept
# alongside the digest for readability -- Docker resolves the digest and ignores the tag. The digest is the
# multi-arch manifest LIST, which is what keeps `--platform=linux/amd64` resolving.
#
# To move it: resolve the new digest and confirm it is a manifest list carrying linux/amd64 BEFORE editing.
#   TOK=$(curl -s "https://public.ecr.aws/token/?scope=repository:lambda/python:pull&service=public.ecr.aws" | jq -r .token)
#   curl -sI -H "Authorization: Bearer $TOK" \
#        -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json" \
#        https://public.ecr.aws/v2/lambda/python/manifests/3.12 | grep -i docker-content-digest
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12@sha256:32c247e8b227e6d1ef8b7f7a6d18dfa544b71b08f830d97d997fd68648d9d3fa

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# shadow-utils supplies useradd; the AL2023 minimal base image does not carry it.
RUN dnf install -y shadow-utils && dnf clean all

# Every wheel here is self-contained (manylinux: Pillow, pypdfium2, imageio-ffmpeg), so no native development
# packages are installed. The pins include the transitive closure.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# The bundled ffmpeg under a stable path. imageio-ffmpeg's own resolver would otherwise execute the binary
# (`ffmpeg -version`) on every cold start to validate it, and the versioned file name moves with the wheel.
RUN FFMPEG_BIN="$(python -c 'import glob, os, imageio_ffmpeg; print(glob.glob(os.path.join(os.path.dirname(imageio_ffmpeg.__file__), "binaries", "ffmpeg-*"))[0])')" \
    && chmod a+rx "${FFMPEG_BIN}" \
    && ln -s "${FFMPEG_BIN}" /usr/local/bin/ffmpeg
ENV IMAGEIO_FFMPEG_EXE=/usr/local/bin/ffmpeg

COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY customLogging ${LAMBDA_TASK_ROOT}/customLogging
COPY media_extractors ${LAMBDA_TASK_ROOT}/media_extractors

# Lambda runs the function as its own least-privileged default user whatever USER says, so the task root is
# made world-readable for that user; the explicit non-root USER covers the runtime interface emulator and any
# other host that honours it. Only /tmp is writable at run time, and the handler works there. useradd is
# called by its full path: the base image's PATH carries /usr/bin but not /usr/sbin.
RUN /usr/sbin/useradd --create-home --uid 10001 --user-group appuser \
    && chmod -R a+rX ${LAMBDA_TASK_ROOT}
ENV HOME=/home/appuser
USER appuser

ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD ["handler.lambda_handler"]
