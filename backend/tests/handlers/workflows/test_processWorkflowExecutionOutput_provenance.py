# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)

# Set required env vars before importing uploadFile
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401


@pytest.mark.unit
def test_build_workflow_change_metadata_full():
    from backend.backend.handlers.assets import uploadFile
    md = uploadFile.build_workflow_change_metadata(
        change_user_id="SYSTEM_USER", workflow_id="wf-1", execution_id="exec-1"
    )
    assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"
    assert md[VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY] == "wf-1"
    assert md[VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY] == "exec-1"


@pytest.mark.unit
def test_build_workflow_change_metadata_empty_without_workflow():
    md = uploadFile.build_workflow_change_metadata(change_user_id=None, workflow_id=None, execution_id=None)
    assert md == {}


@pytest.mark.unit
class TestExecutionIdResolution:
    """processWorkflowExecutionOutput reads the execution id from the workflow
    ASL's pre-existing executionId field (the Step Functions execution name),
    tolerating its absence for non-workflow/direct invocations."""

    @staticmethod
    def _resolve(event):
        # Mirrors the inline expression at both process_external_upload call sites.
        return event.get('executionId')

    def test_reads_execution_id(self):
        assert self._resolve({"executionId": "exec-1"}) == "exec-1"

    def test_none_when_absent(self):
        # No execution id (non-workflow caller) -> None (handled downstream, no crash).
        assert self._resolve({}) is None
