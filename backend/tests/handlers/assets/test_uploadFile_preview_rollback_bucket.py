# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rollback target for the synchronous external-upload preview validation.

A workflow write-back stages its outputs in the run bucket and passes it as
``sourceBucket``, so the destination asset bucket and the bucket holding the ``tempKey``
objects differ. When the batch preview/base-file validation rejects a staged preview,
the temp object it discards is the one it read, so the delete must target the source
bucket -- a delete against the destination bucket is a no-op that leaves the rejected
preview in the run bucket.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

DESTINATION_BUCKET = "asset-bucket"
RUN_BUCKET = "run-bucket"
PREVIEW_RELATIVE_KEY = "/scan.laz.previewFile.gif"
PREVIEW_TEMP_KEY = "temp/up-1/scan.laz.previewFile.gif"


def _external_request(**overrides):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    body = {
        "assetId": "asset-1",
        "databaseId": "db-1",
        "uploadType": "assetFile",
        "files": [{"relativeKey": PREVIEW_RELATIVE_KEY, "tempKey": PREVIEW_TEMP_KEY}],
    }
    body.update(overrides)
    return CompleteExternalUploadRequestModel(**body)


def _complete_with_rejected_preview(request_model):
    """Run complete_external_upload with the batch preview validation rejecting the file.

    Returns (delete_s3_object mock, response).
    """
    from backend.backend.handlers.assets import uploadFile as uf

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
        'bucketName': DESTINATION_BUCKET,
        'baseAssetsPrefix': '',
    }), patch.object(uf, 'get_database_details', return_value={
        'databaseId': 'db-1',
    }), patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'validate_preview_files_with_base_files',
                         return_value=(False, "missing base file", [PREVIEW_RELATIVE_KEY])), \
            patch.object(uf, 'delete_s3_object') as mock_delete:
        mock_s3.head_object.return_value = {'ContentLength': 1024}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        response = uf.complete_external_upload("up-1", request_model, {})

    return mock_delete, response


@pytest.mark.unit
class TestExternalUploadPreviewRollbackBucket:
    def test_rollback_deletes_the_temp_object_from_the_source_bucket(self):
        mock_delete, response = _complete_with_rejected_preview(
            _external_request(sourceBucket=RUN_BUCKET))

        assert response.overallSuccess is False
        mock_delete.assert_called_once_with(RUN_BUCKET, PREVIEW_TEMP_KEY)

    def test_rollback_does_not_target_the_destination_bucket(self):
        mock_delete, _ = _complete_with_rejected_preview(
            _external_request(sourceBucket=RUN_BUCKET))

        targeted_buckets = {call.args[0] for call in mock_delete.call_args_list}
        assert DESTINATION_BUCKET not in targeted_buckets

    def test_rollback_targets_the_asset_bucket_when_no_source_bucket_is_supplied(self):
        """POSITIVE CONTROL: a plain external upload stages into the asset's own bucket."""
        mock_delete, _ = _complete_with_rejected_preview(_external_request())

        mock_delete.assert_called_once_with(DESTINATION_BUCKET, PREVIEW_TEMP_KEY)
