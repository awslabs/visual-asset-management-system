# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_UPLOAD,
)

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.assets import uploadFile  # noqa: F401


@pytest.mark.unit
def test_build_upload_change_metadata_sets_type_and_user():
    from backend.backend.handlers.assets import uploadFile
    md = uploadFile.build_upload_change_metadata("alice@corp")
    assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_UPLOAD
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "alice@corp"


@pytest.mark.unit
def test_build_upload_change_metadata_defaults_user_to_system():
    from backend.backend.handlers.assets import uploadFile
    md = uploadFile.build_upload_change_metadata(None)
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"


@pytest.mark.unit
def test_external_upload_without_workflow_falls_back_to_upload():
    """Verify external uploads without workflow context use upload changeSource."""
    from backend.backend.handlers.assets import uploadFile
    change_metadata = uploadFile.build_workflow_change_metadata(None, None, None) or uploadFile.build_upload_change_metadata("alice")
    assert change_metadata[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_UPLOAD
    assert change_metadata[VAMS_CHANGE_USER_ID_METADATA_KEY] == "alice"


@pytest.mark.unit
def test_external_upload_with_workflow_uses_workflow_type():
    """Verify external uploads with workflow context use workflowExecution changeSource."""
    from backend.backend.handlers.assets import uploadFile
    from common.s3MetadataKeys import (
        VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
        VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
        VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    )
    change_metadata = uploadFile.build_workflow_change_metadata("SYSTEM_USER", "wf-1", "exec-1") or uploadFile.build_upload_change_metadata("SYSTEM_USER")
    assert change_metadata[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
    assert change_metadata[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"
    assert change_metadata[VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY] == "wf-1"
    assert change_metadata[VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY] == "exec-1"


@pytest.mark.unit
def test_external_complete_model_accepts_workflow_fields():
    """Verify CompleteExternalUploadRequestModel accepts and validates workflow provenance fields."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    m = CompleteExternalUploadRequestModel(
        assetId="test-asset",
        databaseId="db-123",
        uploadType="assetFile",
        files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
        workflowId="wf-abc123",
        workflowExecutionId="b9a3aba3c092475f978ad39e5d5a2657",
        changeUserId="SYSTEM_USER",
    )
    assert m.workflowId == "wf-abc123"
    assert m.workflowExecutionId == "b9a3aba3c092475f978ad39e5d5a2657"
    assert m.changeUserId == "SYSTEM_USER"


@pytest.mark.unit
def test_external_complete_model_workflow_fields_optional():
    """Verify workflow provenance fields are optional and default to None."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    m = CompleteExternalUploadRequestModel(
        assetId="test-asset",
        databaseId="db-123",
        uploadType="assetFile",
        files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
    )
    assert m.workflowId is None
    assert m.workflowExecutionId is None
    assert m.changeUserId is None


@pytest.mark.unit
def test_external_complete_model_accepts_system_user():
    """Verify changeUserId accepts SYSTEM_USER."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    m = CompleteExternalUploadRequestModel(
        assetId="test-asset",
        databaseId="db-123",
        uploadType="assetFile",
        files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
        changeUserId="SYSTEM_USER",
    )
    assert m.changeUserId == "SYSTEM_USER"


@pytest.mark.unit
def test_external_complete_model_rejects_invalid_workflow_id():
    """Verify invalid workflowId fails validation."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        CompleteExternalUploadRequestModel(
            assetId="test-asset",
            databaseId="db-123",
            uploadType="assetFile",
            files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
            workflowId="invalid id with spaces!@#",
        )
    error_str = str(exc_info.value).lower()
    assert "workflowid" in error_str  # Field name in error message


@pytest.mark.unit
def test_external_complete_model_rejects_invalid_change_user_id():
    """Verify invalid changeUserId fails validation."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    from pydantic import ValidationError
    # Try creating the model with invalid changeUserId
    try:
        m = CompleteExternalUploadRequestModel(
            assetId="test-asset",
            databaseId="db-123",
            uploadType="assetFile",
            files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
            changeUserId="a!",  # Invalid chars (< 3 valid chars, has !)
        )
        # If we get here, validation did not raise an error - fail the test
        pytest.fail(f"Expected ValidationError but model was created successfully with changeUserId={m.changeUserId}")
    except ValidationError as e:
        error_str = str(e).lower()
        assert "changeuserid" in error_str  # Field name in error message


@pytest.mark.unit
class TestWorkflowExecutionIdProvenanceIsGuidValidated:
    """The provenance field records WHICH execution produced a file, so it takes an execution id.

    Every writer supplies one from `executionRecords.new_guid()` (32 hex) or the dashed uuid Step
    Functions assigns as an execution name. Validating it as STRING_256 accepted arbitrary text into a
    field read back as provenance, so it now uses the same GUID validator the execution routes apply.
    """

    def _model(self, execution_id):
        from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
        return CompleteExternalUploadRequestModel(
            assetId="test-asset",
            databaseId="db-123",
            uploadType="assetFile",
            files=[{"relativeKey": "/a.glb", "tempKey": "temp/upload123/a.glb"}],
            workflowId="wf-abc123",
            workflowExecutionId=execution_id,
            changeUserId="SYSTEM_USER",
        )

    @pytest.mark.parametrize("execution_id", [
        "b9a3aba3c092475f978ad39e5d5a2657",       # new_guid(): 32 lowercase hex
        "b9a3aba3-c092-475f-978a-d39e5d5a2657",   # the dashed uuid Step Functions assigns
        "B9A3ABA3-C092-475F-978A-D39E5D5A2657",   # the dashed form is case-insensitive
    ])
    def test_a_real_execution_id_is_accepted(self, execution_id):
        assert self._model(execution_id).workflowExecutionId == execution_id

    @pytest.mark.parametrize("bad", [
        "exec-xyz789",                             # the placeholder shape this once allowed
        "not-an-execution-id",
        "B9A3ABA3C092475F978AD39E5D5A2657",        # undashed stays lowercase-only (exact DDB key)
        "b9a3aba3c092475f978ad39e5d5a26",          # too short
        "'; DROP TABLE assets; --",
    ])
    def test_a_value_that_is_not_an_execution_id_is_rejected(self, bad):
        with pytest.raises(Exception):
            self._model(bad)
