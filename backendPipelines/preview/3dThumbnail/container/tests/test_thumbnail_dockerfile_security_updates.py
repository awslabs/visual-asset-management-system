#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Both stages of the 3D thumbnail image apply the Debian security updates in the layer that installs packages.

The base image is pinned by digest, so it carries the Debian packages as they were the day that digest
was built; without an upgrade every OS fix published since stays out of the image until someone moves
the pin, and the image shipped with Critical findings that had fixed versions available. AWS Batch on
AWS Fargate patches the host, not the image's own packages. The runtime stage is the image a job pulls;
the builder stage is held to the same rule because the compilers and the venv it produces run on its
packages.

Read from the Dockerfile itself, instruction by instruction, with continuation lines joined, and split
into stages at each FROM.
"""

import pathlib
import re

import pytest

DOCKERFILE = pathlib.Path(__file__).resolve().parents[1] / "Dockerfile"

_PINNED_BASE = re.compile(
    r"^FROM --platform=linux/amd64 public\.ecr\.aws/docker/library/python:3\.12-slim"
    r"@(sha256:[0-9a-f]{64})(?: AS \w+)?$"
)


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


def _stages():
    stages = []
    for instruction in _instructions():
        if instruction.startswith("FROM "):
            stages.append([instruction])
        else:
            assert stages, f"instruction before the first FROM: {instruction}"
            stages[-1].append(instruction)
    return stages


def _package_layer(stage):
    layers = [i for i in stage if i.startswith("RUN ") and "apt-get install" in i]
    assert len(layers) == 1, (stage[0], layers)
    return layers[0]


@pytest.mark.unit
class TestTheImageTakesDebianSecurityUpdates:
    def test_the_dockerfile_keeps_its_builder_and_runtime_stages(self):
        # Control: a parse that saw one stage would let the per-stage checks below skip the builder.
        stages = _stages()
        assert len(stages) == 2, [s[0] for s in stages]
        assert stages[0][0].endswith(" AS builder"), stages[0][0]

    @pytest.mark.parametrize("stage_index", [0, 1], ids=["builder", "runtime"])
    def test_each_package_layer_upgrades_before_it_installs(self, stage_index):
        layer = _package_layer(_stages()[stage_index])
        update, upgrade, install = (layer.find(s) for s in
                                    ("apt-get update", "apt-get upgrade -y", "apt-get install"))
        assert 0 <= update < upgrade < install, layer

    @pytest.mark.parametrize("stage_index", [0, 1], ids=["builder", "runtime"])
    def test_each_package_layer_installs_without_recommends_and_removes_the_lists(self, stage_index):
        layer = _package_layer(_stages()[stage_index])
        assert "apt-get install -y --no-install-recommends" in layer, layer
        assert layer.endswith("rm -rf /var/lib/apt/lists/*"), layer

    def test_both_stages_pin_the_same_base_by_digest(self):
        digests = []
        for stage in _stages():
            match = _PINNED_BASE.match(stage[0])
            assert match, stage[0]
            digests.append(match.group(1))
        assert len(set(digests)) == 1, digests

    def test_the_runtime_stage_still_drops_to_a_non_root_user(self):
        users = [i for i in _stages()[-1] if i.startswith("USER ")]
        assert users and users[-1] not in ("USER root", "USER 0"), users
