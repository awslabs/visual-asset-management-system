# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preview-extension validation at upload initialization.

The completion step rejects a preview whose extension is not an allowed image type, so a
preview accepted at initialization has already been handed presigned part URLs and any
parts the client pushed are left as an incomplete multipart upload. Initialization
therefore applies the same extension rule the completion applies -- for a direct
assetPreview upload and for a ``.previewFile.`` file inside an assetFile upload -- and it
applies it whether or not the asset already has a preview.
"""

import os
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
CLAIMS = {"tokens": ["alice@corp"]}


def _asset(**overrides):
    item = {
        'databaseId': DATABASE_ID,
        'assetId': ASSET_ID,
        'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
    }
    item.update(overrides)
    return item


def _request(uploadType, relativeKey):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    return InitializeUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType=uploadType,
        files=[{"relativeKey": relativeKey, "file_size": 1024}],
    )


@contextmanager
def _initialize_upload_env(asset):
    """Yield (module, s3 mock) with everything initialize_upload touches stubbed."""
    from backend.backend.handlers.assets import uploadFile as uf

    with patch.object(uf, 'check_user_rate_limit', return_value=True), \
            patch.object(uf, 'get_asset_details', return_value=asset), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1',
                'bucketName': 'asset-bucket',
                'baseAssetsPrefix': '',
            }), \
            patch.object(uf, 'validateUnallowedFileExtensionAndContentType', return_value=True), \
            patch.object(uf, 'save_upload_details', MagicMock()), \
            patch.object(uf, 'generate_presigned_url', return_value='https://example.invalid/part'), \
            patch.object(uf, 's3', MagicMock()) as mock_s3:
        mock_s3.create_multipart_upload.return_value = {'UploadId': 's3-upload-1'}
        yield uf, mock_s3


@pytest.mark.unit
class TestAssetPreviewExtensionAtInitialization:
    def test_first_preview_upload_with_a_non_image_extension_is_rejected(self):
        """The asset has no previewLocation -- the common first-upload case."""
        with _initialize_upload_env(_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(_request("assetPreview", "notes.pdf"), CLAIMS)

        assert ".png" in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_replacement_preview_with_a_non_image_extension_is_still_rejected(self):
        """POSITIVE CONTROL: the already-has-a-preview case keeps rejecting."""
        asset = _asset(previewLocation={'Key': 'previews/asset-1/old.png'})
        with _initialize_upload_env(asset) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(_request("assetPreview", "notes.pdf"), CLAIMS)

        assert ".png" in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_first_preview_upload_with_an_image_extension_is_accepted(self):
        """POSITIVE CONTROL: a legitimate first preview still initializes."""
        with _initialize_upload_env(_asset()) as (uf, mock_s3):
            response = uf.initialize_upload(_request("assetPreview", "thumb.png"), CLAIMS)

        assert response.uploadId
        assert [f.relativeKey for f in response.files] == ["thumb.png"]
        mock_s3.create_multipart_upload.assert_called_once()

    def test_replacement_preview_with_an_image_extension_is_accepted(self):
        """POSITIVE CONTROL: replacing an existing preview still initializes."""
        asset = _asset(previewLocation={'Key': 'previews/asset-1/old.png'})
        with _initialize_upload_env(asset) as (uf, mock_s3):
            response = uf.initialize_upload(_request("assetPreview", "thumb.jpg"), CLAIMS)

        assert response.uploadId
        mock_s3.create_multipart_upload.assert_called_once()


@pytest.mark.unit
class TestPreviewFileExtensionInAssetFileUpload:
    def test_preview_file_with_a_non_image_extension_is_rejected(self):
        with _initialize_upload_env(_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(
                    _request("assetFile", "/scan.laz.previewFile.pdf"), CLAIMS)

        assert ".png" in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_preview_file_with_an_image_extension_is_accepted(self):
        """POSITIVE CONTROL: a valid .previewFile. companion still initializes."""
        with _initialize_upload_env(_asset()) as (uf, mock_s3):
            response = uf.initialize_upload(
                _request("assetFile", "/scan.laz.previewFile.png"), CLAIMS)

        assert response.uploadId
        mock_s3.create_multipart_upload.assert_called_once()

    def test_ordinary_asset_file_is_not_subjected_to_the_preview_rule(self):
        """POSITIVE CONTROL: a non-preview file keeps its own extension rules."""
        with _initialize_upload_env(_asset()) as (uf, mock_s3):
            response = uf.initialize_upload(_request("assetFile", "/out/scan.laz"), CLAIMS)

        assert response.uploadId
        mock_s3.create_multipart_upload.assert_called_once()
