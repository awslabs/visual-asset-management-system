# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Change provenance and staging behavior on the >1GB asynchronous upload path.

Covers the hand-off from uploadFile.complete_external_upload to the
sqsUploadFileLarge processor: the queued message must carry the workflow context so
the finalized object is stamped vams-changesource=workflowExecution (the loop guard
sqsBucketSync reads), and an externally staged temp object must not be replaced by a
zero-byte object.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level imports ensure the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402


def _external_request(**overrides):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    body = {
        "assetId": "asset-1",
        "databaseId": "db-1",
        "uploadType": "assetFile",
        "files": [{"relativeKey": "/out/scan.laz", "tempKey": "temp/up-1/scan.laz"}],
    }
    body.update(overrides)
    return CompleteExternalUploadRequestModel(**body)


def _queue_external_upload(request_model, file_size):
    """Run complete_external_upload with a stubbed environment; return queued file_info."""
    from backend.backend.handlers.assets import uploadFile as uf

    captured = {}

    def _capture(file_info, queue_url):
        captured.update(file_info)
        return True

    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': 'asset-1',
        'databaseId': 'db-1',
        'uploadType': 'assetFile',
        'isExternalUpload': True,
        'temporaryPrefix': 'temp/up-1/',
    }), patch.object(uf, 'get_asset_details', return_value={
        'assetId': 'asset-1',
        'databaseId': 'db-1',
        'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
    }), patch.object(uf, 'get_default_bucket_details', return_value={
        'bucketId': 'bucket-1',
        'bucketName': 'asset-bucket',
        'baseAssetsPrefix': '',
    }), patch.object(uf, 'get_database_details', return_value={
        'databaseId': 'db-1',
    }), patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'queue_large_file_for_processing', side_effect=_capture):
        mock_s3.head_object.return_value = {'ContentLength': file_size}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        response = uf.complete_external_upload("up-1", request_model, {})

    return captured, response


@pytest.mark.unit
class TestExternalLargeFileQueueing:
    def test_workflow_context_is_queued_for_large_file(self):
        """A >1GB workflow output carries its workflow provenance into the SQS message."""
        size = uploadFile.LARGE_FILE_THRESHOLD_BYTES + 1
        file_info, response = _queue_external_upload(
            _external_request(workflowId="wf-1", workflowExecutionId="b9a3aba3c092475f978ad39e5d5a2657", changeUserId="SYSTEM_USER"),
            size,
        )

        assert file_info, "large external file was not queued for asynchronous processing"
        assert file_info["workflowId"] == "wf-1"
        assert file_info["workflowExecutionId"] == "b9a3aba3c092475f978ad39e5d5a2657"
        assert file_info["changeUserId"] == "SYSTEM_USER"
        assert response.largeFileAsynchronousHandling is True

    def test_non_workflow_large_file_has_no_workflow_context(self):
        """A plain external upload queues without workflow provenance."""
        size = uploadFile.LARGE_FILE_THRESHOLD_BYTES + 1
        file_info, _ = _queue_external_upload(_external_request(), size)

        assert file_info, "large external file was not queued for asynchronous processing"
        assert file_info["workflowId"] is None
        assert file_info["workflowExecutionId"] is None
        assert file_info["changeUserId"] == "alice@corp"


@pytest.mark.unit
class TestLargeFileChangeMetadataResolution:
    def test_workflow_context_resolves_to_workflow_execution_source(self):
        md = sqsUploadFileLarge.resolve_change_metadata({
            "changeUserId": "SYSTEM_USER",
            "workflowId": "wf-1",
            "workflowExecutionId": "exec-1",
        })
        assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"
        assert md[VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY] == "wf-1"
        assert md[VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY] == "exec-1"

    def test_no_workflow_context_resolves_to_upload_source(self):
        md = sqsUploadFileLarge.resolve_change_metadata({"changeUserId": "bob@corp"})
        assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_UPLOAD
        assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "bob@corp"

    def test_copy_stamps_workflow_source_for_workflow_output(self):
        """The finalized object copy carries the workflowExecution change source."""
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        file_info = {
            "bucketName": "asset-bucket",
            "sourceBucketName": "run-bucket",
            "tempS3Key": "temp/up-1/scan.laz",
            "finalS3Key": "asset-1/out/scan.laz",
            "relativeKey": "/out/scan.laz",
            "uploadType": "assetFile",
            "databaseId": "db-1",
            "assetId": "asset-1",
            "changeUserId": "SYSTEM_USER",
            "workflowId": "wf-1",
            "workflowExecutionId": "exec-1",
        }
        with patch.object(sq, 'validateS3AssetExtensionsAndContentType', return_value=True), \
                patch.object(sq, 'copy_s3_object', return_value=True) as mock_copy, \
                patch.object(sq, 'delete_s3_object', MagicMock()), \
                patch.object(sq, 'update_asset_after_file_processing', MagicMock()):
            assert sq.validate_and_move_large_file(file_info, {}) is True

        change_metadata = mock_copy.call_args.kwargs['change_metadata']
        assert change_metadata[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert change_metadata[VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY] == "exec-1"


@pytest.mark.unit
class TestExternalUploadCompletionShortCircuit:
    def test_external_upload_does_not_materialize_zero_byte_object(self):
        """An externally staged temp object is left untouched by the completion step."""
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        file_info = {
            "bucketName": "asset-bucket",
            "sourceBucketName": "asset-bucket",
            "tempS3Key": "temp/up-1/scan.laz",
            "finalS3Key": "asset-1/out/scan.laz",
            "relativeKey": "/out/scan.laz",
            "uploadType": "assetFile",
            "uploadIdS3": sq.EXTERNAL_UPLOAD_ID,
            "parts": [],
            "uploadId": "up-1",
            "databaseId": "db-1",
            "assetId": "asset-1",
        }
        with patch.object(sq, 's3', MagicMock()) as mock_s3, \
                patch.object(sq, 'create_zero_byte_file') as mock_zero_byte:
            assert sq.complete_multipart_upload_for_large_file(file_info, {}) is True

        mock_zero_byte.assert_not_called()
        mock_s3.put_object.assert_not_called()
        mock_s3.abort_multipart_upload.assert_not_called()

    def test_abandoned_multipart_upload_still_creates_zero_byte_object(self):
        """A real multipart upload with no parts keeps the abandoned-upload behavior."""
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        file_info = {
            "bucketName": "asset-bucket",
            "tempS3Key": "temp/up-1/empty.txt",
            "finalS3Key": "asset-1/empty.txt",
            "relativeKey": "/empty.txt",
            "uploadType": "assetFile",
            "uploadIdS3": "real-s3-upload-id",
            "parts": [],
            "uploadId": "up-1",
            "databaseId": "db-1",
            "assetId": "asset-1",
        }
        with patch.object(sq, 's3', MagicMock()), \
                patch.object(sq, 'create_zero_byte_file', return_value=True) as mock_zero_byte:
            assert sq.complete_multipart_upload_for_large_file(file_info, {}) is True

        mock_zero_byte.assert_called_once()
