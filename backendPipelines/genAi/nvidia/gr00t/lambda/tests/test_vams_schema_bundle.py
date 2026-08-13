#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the gr00t fine-tune vamsSchema bundle.

Execute-time validation reads the WORKFLOW record's assetScope, so this asset-level pipeline only
runs when its workflow bundle declares the matching whole-asset scope."""

import os
import json

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "vamsSchema"))


def _load(name):
    with open(os.path.join(_SCHEMA_ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestGr00tBundle:
    def test_whole_asset_pipeline_has_whole_asset_workflow_scope(self):
        pipeline_scope = _load("pipeline.json")["systemConfig"]["assetScope"]
        workflow_scope = _load("workflow.json")["systemConfig"]["assetScope"]
        assert pipeline_scope.get("wholeAsset") is True
        assert workflow_scope["wholeAssetAllowed"] is True

    def test_workflow_arity_and_metadata_match_the_pipeline(self):
        pipeline_config = _load("pipeline.json")["systemConfig"]
        workflow_config = _load("workflow.json")["systemConfig"]
        assert workflow_config["inputFileArity"] == pipeline_config["inputFileArity"]
        assert workflow_config["metadataInputs"] == pipeline_config["metadataInputs"]
