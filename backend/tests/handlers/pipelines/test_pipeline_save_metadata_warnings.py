# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadataInputs warnings a pipeline save returns: the same non-blocking text a workflow save
reports for that pipeline, so the setting is flagged where it is made."""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import executionValidation as ev
from backend.backend.handlers.pipelines import pipelineService as ps


def _save_warnings(metadata_inputs, **system_config):
    item = {"databaseId": "db1", "pipelineId": "p-inert",
            "systemConfig": {"metadataInputs": metadata_inputs, **system_config}}
    with patch.object(ps, "_workflow_table", MagicMock()):
        return item, ps._pipeline_save_warnings(item)


@pytest.mark.unit
class TestInertAssetFileExpansionWarning:

    @pytest.mark.parametrize("metadata_inputs, warned", [
        ({"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False}, True),
        ({"assetFileMetadata": True, "fileMetadata": False}, False),
        ({"assetFileMetadata": True, "fileAttributes": False}, False),
        ({"assetFileMetadata": True}, False),
        ({"assetFileMetadata": False, "fileMetadata": False, "fileAttributes": False}, False),
        ({"fileMetadata": False, "fileAttributes": False}, False),
    ], ids=["both-off", "metadata-off", "attributes-off", "both-on", "key-off", "key-absent"])
    def test_a_save_warns_only_when_the_expansion_would_carry_nothing(self, metadata_inputs, warned):
        item, warnings = _save_warnings(metadata_inputs)
        expected = ev.inert_asset_file_metadata_warnings(item["systemConfig"], "pipeline 'p-inert'")
        if warned:
            # A pipeline's gate narrows only what its step receives: the run still expands under the
            # workflow's file types, so the text names the empty records, not a skipped expansion.
            assert warnings == expected == [
                "pipeline 'p-inert' uses assetFileMetadata with fileMetadata and fileAttributes both "
                "off, so the per-file records it receives carry no metadata or attributes."]
        else:
            assert expected == []
            assert warnings == []

    def test_the_text_matches_the_workflow_save_check(self):
        metadata_inputs = {"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False}
        _item, warnings = _save_warnings(metadata_inputs)
        _errors, workflow_warnings = ev.validate_workflow_save(
            {"metadataInputs": {}},
            [{"pipelineId": "p-inert", "pipelineDatabaseId": "",
              "systemConfig": {"metadataInputs": metadata_inputs}}])
        assert warnings[0] in workflow_warnings

    def test_it_is_reported_beside_the_arity_none_warning(self):
        # Each check reports its own cause, as a workflow save does for the same config.
        _item, warnings = _save_warnings(
            {"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False},
            inputFileArity="none")
        assert len(warnings) == 2
        assert "inputFileArity 'none'" in warnings[0]
        assert "records it receives carry no metadata or attributes" in warnings[1]
        _item, warnings = _save_warnings({"assetFileMetadata": True}, inputFileArity="none")
        # Arity 'none' with the file types on: the arity warning only.
        assert len(warnings) == 1 and "inputFileArity 'none'" in warnings[0]
