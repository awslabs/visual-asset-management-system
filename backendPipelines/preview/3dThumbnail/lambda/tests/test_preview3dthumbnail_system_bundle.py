# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shipped 3D and Media Preview Thumbnail bundles register SYSTEM records: the pipeline and the
workflow of each declare `isSystem: true` and the `SYSTEM - Preview` category, so the deployment owns the
records and the API holds them read-only except for their enabled switches and template content. The media
bundle (vamsSchemaMedia, beside the 3D one) became a system record by owner decision on 2026-11-17; its
name, ids and template are unchanged."""

import json
import os

import pytest

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYSTEM_CATEGORY = "SYSTEM - Preview"

# (bundle folder, record name, record id, default template id)
BUNDLES = [
    ("vamsSchema", "3D Preview Thumbnail", "preview-3d-thumbnail", "preview-3d-thumbnail-default"),
    ("vamsSchemaMedia", "Media Preview Thumbnail", "preview-media-thumbnail", "preview-media-thumbnail-default"),
]


def _read(folder, name):
    with open(os.path.join(_PIPELINE_DIR, folder, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
@pytest.mark.parametrize("folder, record_name, record_id, template_id", BUNDLES, ids=[b[0] for b in BUNDLES])
class TestSystemBundle:
    def test_the_bundle_directory_is_the_one_the_construct_uploads(self, folder, record_name, record_id,
                                                                   template_id):
        bundle_dir = os.path.join(_PIPELINE_DIR, folder)
        assert os.path.isfile(os.path.join(bundle_dir, "pipeline.json")), bundle_dir
        assert os.path.isfile(os.path.join(bundle_dir, "workflow.json")), bundle_dir
        assert os.path.isfile(os.path.join(bundle_dir, "templates", f"{template_id}.json")), bundle_dir

    def test_the_pipeline_is_a_system_record(self, folder, record_name, record_id, template_id):
        pipeline = _read(folder, "pipeline.json")
        assert pipeline["isSystem"] is True
        assert pipeline["category"] == SYSTEM_CATEGORY

    def test_the_workflow_is_a_system_record(self, folder, record_name, record_id, template_id):
        workflow = _read(folder, "workflow.json")
        assert workflow["isSystem"] is True
        assert workflow["category"] == SYSTEM_CATEGORY

    def test_the_system_category_follows_the_convention(self, folder, record_name, record_id, template_id):
        # `SYSTEM - <Area>`: what the API docs and the web badge key on.
        assert SYSTEM_CATEGORY.startswith("SYSTEM - ")
        assert _read(folder, "pipeline.json")["category"] == _read(folder, "workflow.json")["category"]

    def test_the_identity_and_filters_are_unchanged(self, folder, record_name, record_id, template_id):
        pipeline, workflow = _read(folder, "pipeline.json"), _read(folder, "workflow.json")
        assert pipeline["pipelineName"] == record_name
        assert workflow["workflowName"] == record_name
        allow = pipeline["systemConfig"]["inputFileFilters"]["allow"]
        assert allow == workflow["systemConfig"]["inputFileFilters"]["allow"]
        assert allow == workflow["triggers"][0]["inputFileFilters"]["allow"]
        assert workflow["triggers"][0]["defaultTemplateIds"] == {f"GLOBAL:{record_id}": template_id}
