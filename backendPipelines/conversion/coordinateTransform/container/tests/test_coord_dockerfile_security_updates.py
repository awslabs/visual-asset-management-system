#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The coordinate transform image applies the Debian security updates in the layer that installs packages.

The base image is pinned by digest, so it carries the Debian packages as they were the day that digest
was built; without an upgrade every OS fix published since stays out of the image until someone moves
the pin, and the image shipped with Critical findings that had fixed versions available. AWS Batch on
AWS Fargate patches the host, not the image's own packages.

Read from the Dockerfile itself, instruction by instruction, with continuation lines joined.
"""

import pathlib
import re

import pytest

DOCKERFILE = pathlib.Path(__file__).resolve().parents[1] / "Dockerfile"


def _instructions():
    joined, current = [], ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if current and line.startswith("#"):
            continue  # a comment inside a continued instruction
        current = f"{current} {line}" if current else line
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        joined.append(current)
        current = ""
    return joined


def _package_layer():
    layers = [i for i in _instructions() if i.startswith("RUN ") and "apt-get install" in i]
    assert len(layers) == 1, layers
    return layers[0]


@pytest.mark.unit
class TestTheImageTakesDebianSecurityUpdates:
    def test_the_package_layer_upgrades_before_it_installs(self):
        layer = _package_layer()
        update, upgrade, install = (layer.find(s) for s in
                                    ("apt-get update", "apt-get upgrade -y", "apt-get install"))
        assert 0 <= update < upgrade < install, layer

    def test_the_package_layer_removes_the_package_lists(self):
        assert _package_layer().endswith("rm -rf /var/lib/apt/lists/*")

    def test_the_base_stays_pinned_by_digest(self):
        base = [i for i in _instructions() if i.startswith("FROM ")]
        assert len(base) == 1
        assert re.search(r"python:3\.11-slim@sha256:[0-9a-f]{64}$", base[0]), base[0]

    def test_the_image_still_drops_to_a_non_root_user(self):
        users = [i for i in _instructions() if i.startswith("USER ")]
        assert users and users[-1] not in ("USER root", "USER 0")
