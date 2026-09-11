# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preview-extension validation on the external-upload completion path.

An external upload is staged by the caller (a pipeline write-back, or any client holding
the temporary prefix) and then completed through ``complete_external_upload``. Completion
applies the same preview-extension rule the interactive initialize and complete paths
apply -- to a direct assetPreview upload and to a ``.previewFile.`` companion inside an
assetFile upload -- and a refused file is reported in its own file result and never moved
into the asset location. The extension is read after the ``.previewFile.`` marker, not
after the last dot.
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
UPLOAD_ID = "up-1"
TEMP_PREFIX = "temp/up-1/"
ASSET_BUCKET = "asset-bucket"
REFUSAL_FRAGMENT = "allowed extensions"


def _temp_key(relativeKey):
    return TEMP_PREFIX + relativeKey.lstrip("/")


def _external_request(uploadType, *relativeKeys):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType=uploadType,
        files=[{"relativeKey": k, "tempKey": _temp_key(k)} for k in relativeKeys],
    )


@contextmanager
def _complete_external_upload_env(uploadType):
    """Yield (module, mocks) with everything complete_external_upload touches stubbed.

    Every staged object exists (head_object answers 1 KB), the malicious-content scan passes,
    the batch base-file validation passes, and the copy succeeds -- so the only rule that can
    refuse a file in these cases is the one under test.
    """
    from backend.backend.handlers.assets import uploadFile as uf

    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': ASSET_ID,
        'databaseId': DATABASE_ID,
        'uploadType': uploadType,
        'isExternalUpload': True,
        'temporaryPrefix': TEMP_PREFIX,
    }), \
            patch.object(uf, 'get_asset_details', return_value={
                'assetId': ASSET_ID,
                'databaseId': DATABASE_ID,
                'bucketId': 'bucket-1',
                'assetLocation': {'Key': 'asset-1/'},
            }), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1',
                'bucketName': ASSET_BUCKET,
                'baseAssetsPrefix': '',
            }), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'validate_preview_files_with_base_files', return_value=(True, None, [])), \
            patch.object(uf, 'delete_existing_preview_files', return_value=[]), \
            patch.object(uf, 'determine_asset_type', return_value='laz'), \
            patch.object(uf, 'send_subscription_email', MagicMock()), \
            patch.object(uf, 'log_file_upload', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'copy_s3_object', return_value=True) as mock_copy, \
            patch.object(uf, 'delete_s3_object') as mock_delete, \
            patch.object(uf, 'queue_large_file_for_processing', return_value=True) as mock_queue, \
            patch.object(uf, 'update_asset_attributes') as mock_update_asset:
        mock_s3.head_object.return_value = {'ContentLength': 1024}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        mocks = {
            's3': mock_s3,
            'copy': mock_copy,
            'delete': mock_delete,
            'queue': mock_queue,
            'update_asset': mock_update_asset,
        }
        yield uf, mocks


def _result_for(response, relativeKey):
    matches = [r for r in response.fileResults if r.relativeKey == relativeKey]
    assert len(matches) == 1, f"expected exactly one result for {relativeKey}, got {matches}"
    return matches[0]


def _assert_nothing_written(mocks):
    mocks['copy'].assert_not_called()
    mocks['delete'].assert_not_called()
    mocks['queue'].assert_not_called()
    mocks['update_asset'].assert_not_called()


@pytest.mark.unit
class TestPreviewFileExtensionOnExternalCompletion:
    def test_preview_companion_with_a_non_image_extension_is_refused(self):
        companion = "/scan.laz.previewFile.pdf"
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", companion), {})

        assert response.overallSuccess is False
        result = _result_for(response, companion)
        assert result.success is False
        assert REFUSAL_FRAGMENT in result.error
        assert ".png" in result.error
        _assert_nothing_written(mocks)

    def test_a_refused_companion_does_not_block_the_other_files(self):
        """The refusal is per file: the base file still completes, the companion does not move."""
        base = "/scan.laz"
        companion = "/scan.laz.previewFile.pdf"
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", base, companion), {})

        assert response.overallSuccess is False
        assert _result_for(response, base).success is True
        refused = _result_for(response, companion)
        assert refused.success is False
        assert REFUSAL_FRAGMENT in refused.error

        copied_temp_keys = [call.args[1] for call in mocks['copy'].call_args_list]
        assert copied_temp_keys == [_temp_key(base)]

    def test_preview_companion_with_an_image_extension_is_accepted(self):
        """POSITIVE CONTROL: a valid .previewFile. companion still completes and moves."""
        base = "/scan.laz"
        companion = "/scan.laz.previewFile.png"
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", base, companion), {})

        assert response.overallSuccess is True
        assert _result_for(response, base).success is True
        assert _result_for(response, companion).success is True

        copied_temp_keys = sorted(call.args[1] for call in mocks['copy'].call_args_list)
        assert copied_temp_keys == sorted([_temp_key(base), _temp_key(companion)])

    def test_ordinary_asset_file_is_not_subjected_to_the_preview_rule(self):
        """POSITIVE CONTROL: the rule keys on the marker, not the extension -- an ordinary .pdf
        asset file (the same extension refused for a companion) completes untouched."""
        ordinary = "/out/notes.pdf"
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", ordinary), {})

        assert response.overallSuccess is True
        assert _result_for(response, ordinary).success is True
        mocks['copy'].assert_called_once()
        assert mocks['copy'].call_args.args[1] == _temp_key(ordinary)

    def test_extension_is_read_after_the_marker_not_after_the_last_dot(self):
        """A last-dot check passes ``.previewFile.p.png``; the marker split reads ``.p.png``
        and refuses it, the same as the interactive paths."""
        base = "/model.gltf"
        companion = "/model.gltf.previewFile.p.png"
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", base, companion), {})

        refused = _result_for(response, companion)
        assert refused.success is False
        assert REFUSAL_FRAGMENT in refused.error
        copied_temp_keys = [call.args[1] for call in mocks['copy'].call_args_list]
        assert _temp_key(companion) not in copied_temp_keys


@pytest.mark.unit
class TestAssetPreviewExtensionOnExternalCompletion:
    def test_asset_preview_with_a_non_image_extension_is_refused(self):
        with _complete_external_upload_env("assetPreview") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetPreview", "cover.pdf"), {})

        assert response.overallSuccess is False
        result = _result_for(response, "cover.pdf")
        assert result.success is False
        assert REFUSAL_FRAGMENT in result.error
        _assert_nothing_written(mocks)

    def test_asset_preview_with_an_image_extension_is_accepted(self):
        """POSITIVE CONTROL: a legitimate preview completes and is recorded on the asset."""
        with _complete_external_upload_env("assetPreview") as (uf, mocks):
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetPreview", "cover.png"), {})

        assert response.overallSuccess is True
        assert _result_for(response, "cover.png").success is True
        mocks['copy'].assert_called_once()
        mocks['update_asset'].assert_called_once()
        assert 'previewLocation' in mocks['update_asset'].call_args.args[2]
