# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shipped 3D Preview Thumbnail bundle registers a SYSTEM record: both the pipeline and the workflow
declare `isSystem: true` and the `SYSTEM - Preview` category, so the deployment owns the record and the
API holds it read-only except for its enabled switches and template content."""

import json
import os

import pytest

_BUNDLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vamsSchema")

SYSTEM_CATEGORY = "SYSTEM - Preview"


def _read(name):
    with open(os.path.join(_BUNDLE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestSystemBundle:
    def test_the_bundle_directory_is_the_one_the_construct_uploads(self):
        assert os.path.isfile(os.path.join(_BUNDLE_DIR, "pipeline.json")), _BUNDLE_DIR
        assert os.path.isfile(os.path.join(_BUNDLE_DIR, "workflow.json")), _BUNDLE_DIR

    def test_the_pipeline_is_a_system_record(self):
        pipeline = _read("pipeline.json")
        assert pipeline["isSystem"] is True
        assert pipeline["category"] == SYSTEM_CATEGORY

    def test_the_workflow_is_a_system_record(self):
        workflow = _read("workflow.json")
        assert workflow["isSystem"] is True
        assert workflow["category"] == SYSTEM_CATEGORY

    def test_the_system_category_follows_the_convention(self):
        # `SYSTEM - <Area>`: what the API docs and the web badge key on.
        assert SYSTEM_CATEGORY.startswith("SYSTEM - ")
        assert _read("pipeline.json")["category"] == _read("workflow.json")["category"]

    def test_the_identity_and_filters_are_unchanged(self):
        pipeline, workflow = _read("pipeline.json"), _read("workflow.json")
        assert pipeline["pipelineName"] == "3D Preview Thumbnail"
        allow = pipeline["systemConfig"]["inputFileFilters"]["allow"]
        assert allow == workflow["systemConfig"]["inputFileFilters"]["allow"]
        assert allow == workflow["triggers"][0]["inputFileFilters"]["allow"]
        assert workflow["triggers"][0]["defaultTemplateIds"] == {
            "GLOBAL:preview-3d-thumbnail": "preview-3d-thumbnail-default"}
