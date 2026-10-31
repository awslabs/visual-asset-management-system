#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A mesh among the input files is a starting point for one model and an error for every other.

The Blender agent can REFINE an existing model, so a .glb beside the photographs is a legitimate input to
it. Every other model reconstructs from pixels and has no input for a mesh -- and a manifest is what the
execute API hands us, so a mesh can arrive for any model. Refusing it at the Lambda boundary is what keeps
the failure from landing on a GPU instance, after the image pull and the weight restore, as a traceback
inside the container's image validation.

Both Lambdas that see the input list gate it: vamsExecute by model capability, openPipeline by extension.
"""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


@pytest.mark.unit
class TestOpenPipelineExtensionGate:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".png,.jpg,.jpeg")
        monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-west-2:123456789012:stateMachine:x")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        monkeypatch.setenv("ORCHESTRATION_BUS_NAME", "vams-orchestration")

    def test_photographs_are_allowed_for_every_model(self):
        mod = _load("openPipeline")
        for model in ("stable3dgen", "sam3d", "hunyuan3d", "blenderagent"):
            assert ".png" in mod.allowed_extensions(model)

    def test_a_mesh_is_allowed_only_for_the_agent(self):
        mod = _load("openPipeline")
        assert ".glb" in mod.allowed_extensions("blenderagent")
        assert ".stl" in mod.allowed_extensions("blenderagent")
        for model in ("stable3dgen", "sam3d", "hunyuan3d", "trellis2", "triposr", "step1x3d"):
            assert ".glb" not in mod.allowed_extensions(model), f"{model} must not accept a mesh"

    def test_the_gate_reads_the_model_it_is_given_not_a_default(self):
        mod = _load("openPipeline")
        assert ".glb" not in mod.allowed_extensions(None)
        assert ".glb" not in mod.allowed_extensions("")


@pytest.mark.unit
class TestVamsExecuteMeshGate:
    def test_the_agent_is_the_only_model_that_may_take_a_mesh(self):
        mod = _load("vamsExecuteImageTo3dPipeline")
        assert mod.MESH_INPUT_MODELS == ("blenderagent",)
        assert "blenderagent" in mod.MULTI_VIEW_MODELS
        assert mod.MULTI_VIEW_MODELS["blenderagent"] >= 2, "a refine run is a photograph plus a mesh"

    def test_every_mesh_extension_the_container_partitions_on_is_gated_here_too(self):
        """The container splits inputs into images and meshes by extension; the Lambda must recognise the
        same set, or a mesh with an extension only one side knows slips through as a photograph."""
        mod = _load("vamsExecuteImageTo3dPipeline")
        container = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "container", "__main__.py")
        source = open(container, encoding="utf-8").read()
        import re

        match = re.search(r"MESH_INPUT_EXTENSIONS = \(([^)]*)\)", source)
        assert match, "the container declares no MESH_INPUT_EXTENSIONS"
        container_set = set(re.findall(r'"(\.[a-z0-9]+)"', match.group(1)))
        assert set(mod.MESH_INPUT_EXTENSIONS) == container_set
