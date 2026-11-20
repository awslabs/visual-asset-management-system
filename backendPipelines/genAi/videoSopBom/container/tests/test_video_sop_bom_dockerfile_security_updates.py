#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Video SOP/BOM image applies the Debian security updates in the layer that installs ffmpeg.

The base image is pinned by digest, so it carries the Debian packages, ffmpeg's libraries among them,
as they were the day that digest was built; without an upgrade every OS fix published since stays out
of the image until someone moves the pin. AWS Batch on AWS Fargate patches the host, not the image's
own packages.

Read from the Dockerfile itself, instruction by instruction, with continuation lines joined.
"""

import pathlib

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
class TestTheVideoSopBomImageTakesDebianSecurityUpdates:
    def test_the_package_layer_upgrades_before_it_installs(self):
        layer = _package_layer()
        update, upgrade, install = (layer.find(s) for s in
                                    ("apt-get update", "apt-get upgrade -y", "apt-get install"))
        assert 0 <= update < upgrade < install, layer

    def test_the_package_layer_installs_ffmpeg_and_removes_the_package_lists(self):
        layer = _package_layer()
        assert " ffmpeg " in f"{layer} "
        assert layer.endswith("rm -rf /var/lib/apt/lists/*")
