# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    VAMS_CHANGE_SOURCE_VALUES,
    CHANGE_PROVENANCE_METADATA_KEYS,
    is_system_metadata_key,
)


@pytest.mark.unit
class TestChangeProvenanceKeys:
    def test_keys_are_lowercased(self):
        assert VAMS_CHANGE_SOURCE_METADATA_KEY == "vams-changesource"
        assert VAMS_CHANGE_USER_ID_METADATA_KEY == "vams-changeuserid"
        assert VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY == "vams-changeworkflowexecutionid"
        assert VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY == "vams-changeworkflowid"
        assert VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY == "vams-changeassetidfrom"
        assert VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY == "vams-changedatabaseidfrom"
        assert VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY == "vams-changeassetfilepathfrom"
        assert VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY == "vams-changeassetfileversionfrom"

    def test_change_source_values(self):
        assert VAMS_CHANGE_SOURCE_VALUES == frozenset({
            "direct", "upload", "workflowExecution", "fileCopy",
            "fileMove", "fileRename", "fileArchive", "fileUnarchive",
            "assetArchive", "assetUnarchive", "fileRevert",
        })

    def test_provenance_keys_are_system_excluded(self):
        for key in CHANGE_PROVENANCE_METADATA_KEYS:
            assert is_system_metadata_key(key) is True

    def test_provenance_keys_not_searchable(self):
        from common.s3MetadataKeys import SEARCHABLE_VAMS_METADATA_KEYS
        for key in CHANGE_PROVENANCE_METADATA_KEYS:
            assert key not in SEARCHABLE_VAMS_METADATA_KEYS
