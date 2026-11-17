#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the cogConvert vamsSchema bundle: filters, loop guards, trigger default, template."""

import json
import os

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))
_ACCEPTED = {"*.tif", "*.tiff"}
_EXCLUDED = ["*.cog.tif"]


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _templates():
    template_dir = os.path.join(_SCHEMA_ROOT, "templates")
    return [_load("templates", name) for name in sorted(os.listdir(template_dir)) if name.endswith(".json")]


@pytest.mark.unit
class TestCogConvertBundle:
    def test_pipeline_filters_accept_tiffs_and_exclude_its_own_output(self):
        filters = _load("pipeline.json")["systemConfig"]["inputFileFilters"]
        assert set(filters["allow"]) == _ACCEPTED and filters["exclude"] == _EXCLUDED

    def test_the_trigger_has_the_same_filters_as_the_pipeline(self):
        # Two separately stored records: the trigger's filters are PUT to the trigger route.
        trigger = _load("workflow.json")["triggers"][0]
        assert set(trigger["inputFileFilters"]["allow"]) == _ACCEPTED
        assert trigger["inputFileFilters"]["exclude"] == _EXCLUDED

    def test_the_upload_trigger_ships_disabled(self):
        assert _load("workflow.json")["triggers"][0]["enabled"] is False

    def test_the_pipeline_takes_one_file_and_not_the_whole_asset(self):
        system = _load("pipeline.json")["systemConfig"]
        assert system["inputFileArity"] == "one" and system["assetScope"] == {"wholeAsset": False}

    def test_the_pipeline_waits_for_the_callback_and_is_sized_for_scenes(self):
        execution = _load("pipeline.json")["executionConfig"]
        assert execution["executionType"] == "Lambda" and execution["waitForCallback"] == "Enabled"
        assert int(execution["taskTimeout"]) >= 14400

    def test_the_workflow_does_not_allow_chaining_into_itself(self):
        # Default false: a workflow never fires on its own output whatever the value, and other workflows
        # (media preview, SYSTEM metadata) opt in on their own side.
        assert "allowWorkflowTriggerChaining" not in _load("pipeline.json")["systemConfig"]

    def test_trigger_default_template_exists(self):
        trigger = _load("workflow.json")["triggers"][0]
        ids = {t["templateId"] for t in _templates()}
        assert ids and all(value in ids for value in trigger["defaultTemplateIds"].values())

    def test_the_default_template_leaves_the_output_beside_the_input(self):
        # An output path extension would move the COG into a subfolder, and the link attributes name the
        # file beside its source.
        for template in _templates():
            assert "defaultOutputFileBaseExecutionPathExtension" not in template
            assert "defaultOutputFileBaseExecutionPathExtension" not in _load("workflow.json")

    def test_every_template_body_carries_only_allowed_settings(self):
        for template in _templates():
            assert set(json.loads(template["configBody"])) <= {"compression"}

    def test_the_description_names_the_output_crs(self):
        assert "EPSG:3857" in _load("pipeline.json")["description"]
