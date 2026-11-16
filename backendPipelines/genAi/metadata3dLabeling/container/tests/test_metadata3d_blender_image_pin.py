# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender image is pinned to the Alpine release and Blender version its accepted formats were
tested on.

Blender comes from Alpine's `apk`, so the Alpine release decides which Blender is installed and which
importers it has. On a floating `python:3.12-alpine` the image moved to a new Blender on every build:
built on Alpine 3.24 it got Blender 5.1, which has no USD or COLLADA importer, while the pipeline still
accepted `.usd` and `.dae`.

The pair below is the one whose importers were run on a real file of every accepted format. Moving
either pin means re-running that check and updating the accepted formats (the construct's
`allowedInputFileExtensions`, `vamsSchema/`, and `renderScene.py`) before changing this test.
"""

import io
import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
DOCKERFILE = os.path.join(os.path.dirname(_HERE), "Dockerfile_BlenderRenderer")

TESTED_ALPINE_RELEASE = "3.24"
TESTED_BLENDER_MINOR = "5.1"


def _instructions():
    """Dockerfile instructions with comments dropped and continuation lines joined."""
    lines = [line for line in io.open(DOCKERFILE, encoding="utf-8").read().splitlines()
             if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines)).splitlines()


def _from_line():
    froms = [line for line in _instructions() if line.startswith("FROM ")]
    assert len(froms) == 1, f"expected one FROM, found {froms}"
    return froms[0]


def _apk_add_packages():
    packages = []
    for line in _instructions():
        match = re.match(r"^RUN apk add (.*)$", line)
        if match:
            packages += [token for token in match.group(1).split() if not token.startswith("-")]
    return packages


@pytest.mark.unit
def test_base_image_names_the_tested_alpine_release_and_a_digest():
    """A tag alone moves; the digest fixes the image and the tag names the release it is."""
    assert re.match(
        r"^FROM --platform=linux/amd64 public\.ecr\.aws/docker/library/python:3\.12-alpine"
        + re.escape(TESTED_ALPINE_RELEASE) + r"@sha256:[0-9a-f]{64}$",
        _from_line(),
    ), _from_line()


@pytest.mark.unit
def test_blender_is_pinned_to_the_tested_minor_version():
    """`blender~X.Y` keeps the importer set fixed and still resolves after Alpine rebuilds the package."""
    blender = [package for package in _apk_add_packages() if re.match(r"^blender\b", package)]
    assert blender == [f"blender~{TESTED_BLENDER_MINOR}"], blender
