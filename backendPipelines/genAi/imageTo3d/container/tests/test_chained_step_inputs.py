# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What a second pipeline step makes of the first step's whole output.

A workflow's later step receives everything the prior step wrote: the same model as .glb, .obj and .stl,
the OBJ's texture sidecar ``material_0.png``, and -- with PASS_THROUGH_INPUTS -- the photograph. The first
two-step run refused that set: "At most one mesh may be supplied as a starting point; got 3." Three files, one
model. The container now keeps one mesh per stem, preferring the glTF that carries the texture, and does not
mistake the texture sidecar for a reference photograph; the refine pipeline's filters narrow the same way at
the workflow boundary, so either half alone would have carried the run.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPELINE_DIR = os.path.dirname(_CONTAINER_DIR)


@pytest.fixture(scope="module")
def main_module():
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main_chain", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestOneModelInThreeFormatsIsOneStartingMesh:
    def test_the_gltf_is_kept_when_the_same_stem_arrives_as_glb_obj_and_stl(self, main_module):
        kept = main_module.select_starting_meshes(
            [Path("/in/user_supplied.obj"), Path("/in/user_supplied.stl"), Path("/in/user_supplied.glb")])
        assert kept == [Path("/in/user_supplied.glb")]

    def test_different_stems_are_different_models_and_all_stay(self, main_module):
        kept = main_module.select_starting_meshes([Path("/in/a.glb"), Path("/in/b.stl")])
        assert kept == [Path("/in/a.glb"), Path("/in/b.stl")], "the one-mesh rule downstream must still see both"

    def test_without_a_gltf_the_most_faithful_remaining_format_is_kept(self, main_module):
        kept = main_module.select_starting_meshes([Path("/in/m.stl"), Path("/in/m.obj")])
        assert kept == [Path("/in/m.obj")]

    def test_the_selection_is_deterministic(self, main_module):
        assert main_module.select_starting_meshes([Path("/in/b.glb"), Path("/in/a.glb")]) == [Path("/in/a.glb"), Path("/in/b.glb")]


@pytest.mark.unit
class TestTheRefineBundleNarrowsWhatAPriorStepHandsOn:
    def _load(self, *parts):
        with open(os.path.join(_PIPELINE_DIR, *parts), encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_refine_pipeline_takes_photographs_and_the_gltf_only(self):
        filters = self._load("vamsSchema-refine", "pipeline.json")["systemConfig"]["inputFileFilters"]
        assert set(filters["allow"]) == {"*.png", "*.jpg", "*.jpeg", "*.glb"}
        assert "material*.png" in filters["exclude"] and "*.previewFile.*" in filters["exclude"]

    def test_the_refine_templates_exclude_the_obj_texture_sidecar(self):
        for path in [(bundle, "templates", f"image-to-3d-blenderagent-{name}.json")
                     for bundle in ("vamsSchema-refine", "vamsSchema") for name in ("refine", "refit", "refit-agent")]:
            filters = self._load(*path)["overrides"]["inputFileFilters"]
            assert "material*.png" in filters["exclude"], path

    def test_the_two_step_workflow_lists_the_reconstruction_pipeline_then_the_refine(self):
        workflow = self._load("vamsSchema-refine", "workflow.json")
        assert [p["pipelineId"] for p in workflow["specifiedPipelines"]] == ["genai-image-to-3d", "genai-image-to-3d-refine"]
        assert workflow["systemConfig"]["inputFileArity"] == "multi"

    def test_the_refine_bundle_template_is_its_pipelines_default(self):
        template = self._load("vamsSchema-refine", "templates", "image-to-3d-blenderagent-refine.json")
        assert template["isDefault"] is True
        assert template["templateId"] == "image-to-3d-blenderagent-refine"

    def test_the_first_step_template_passes_the_photograph_on(self):
        template = self._load("vamsSchema", "templates", "image-to-3d-sam3d-then-refine.json")
        field = next(f for f in template["tagSchema"] if f["tagKey"] == "PASS_THROUGH_INPUTS")
        assert field["default"] == "true"
        assert '"PASS_THROUGH_INPUTS": "{{PASS_THROUGH_INPUTS}}"' in template["configBody"]
