# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every upload entry point applies the database's extension restriction the same way (#371 IF-044).

The rule itself is pinned in tests/common/test_database_extension_rule.py. Here each entry point
that reads restrictFileUploadsToExtensions is driven with a multi-part (.tar.gz) and an underscore
(.x_t) entry: upload initialization, the user upload completion and the external (workflow output)
completion. The ingest-asset initialize stage is covered in test_ingestAsset_initialize_refusal.py.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from tests.handlers.assets import test_uploadFile_completion_blocklist as completion  # noqa: E402
from tests.handlers.assets import test_uploadFile_preview_extension_init as init  # noqa: E402

RESTRICTION = ".x_t,.tar.gz"
REFUSAL = "Database does not allow this file extension"
DATABASE = {"databaseId": completion.DATABASE_ID, "restrictFileUploadsToExtensions": RESTRICTION}

ALLOWED = ["/part.x_t", "/PART.X_T", "/data/archive.tar.gz", "/ARCHIVE.TAR.GZ"]
# A name ending in a dot ("file.") or holding ".." ("..x_t") never reaches these checks: the request
# models refuse it.
REFUSED = ["/archive.gz", "/part.xt", "/README", "/.x_t"]


def _initialize(relativeKey):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    request = InitializeUploadRequestModel(
        assetId=init.ASSET_ID, databaseId=init.DATABASE_ID, uploadType="assetFile",
        files=[{"relativeKey": relativeKey, "file_size": 1024}])
    with init._initialize_upload_env(init._asset()) as (uf, mock_s3), \
            patch.object(uf, "get_database_details", return_value=DATABASE):
        try:
            uf.initialize_upload(request, init.CLAIMS)
        except uf.VAMSGeneralErrorResponse as error:
            return str(error), mock_s3
    return None, mock_s3


def _complete(relativeKey):
    record = completion._upload_record([relativeKey])
    with completion._completion_env(record) as (uf, mock_s3, mock_copy), \
            patch.object(uf, "get_database_details", return_value=DATABASE):
        response = uf.complete_upload(completion.UPLOAD_ID,
                                      completion._request(completion._multipart(relativeKey)),
                                      completion._rest_event())
    return completion._result(response, relativeKey), mock_copy


def _complete_external(relativeKey):
    """The external completion's per-file result: the extension check runs before any copy."""
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    request = CompleteExternalUploadRequestModel.construct(
        assetId=completion.ASSET_ID, databaseId=completion.DATABASE_ID, uploadType="assetFile",
        workflowId=None, workflowExecutionId=None, changeUserId=None, sourceBucket=None,
        files=[MagicMock(relativeKey=relativeKey, tempKey="temp/up-1/x")])
    uf = uploadFile
    stop = RuntimeError("stop after the per-file checks")
    with patch.object(uf, "get_default_bucket_details", return_value={
        "bucketId": "bucket-1", "bucketName": completion.BUCKET, "baseAssetsPrefix": ""}), \
            patch.object(uf, "external_upload_source", return_value={"bucketName": completion.BUCKET}), \
            patch.object(uf, "get_database_details", return_value=DATABASE), \
            patch.object(uf, "validateUnallowedFileExtensionAndContentType", return_value=True), \
            patch.object(uf, "FileCompletionResult", side_effect=lambda **kw: kw) as result, \
            patch.object(uf, "s3", MagicMock()) as mock_s3:
        # The first S3 read after the extension check ends the call: only the check is under test.
        mock_s3.head_object.side_effect = stop
        try:
            uf._complete_claimed_external_upload(
                completion.UPLOAD_ID, request, {}, {}, completion._asset(), "alice@corp", {})
        except Exception:
            pass
    errors = [call.kwargs.get("error") or "" for call in result.call_args_list]
    return [e for e in errors if e.startswith(REFUSAL)]


@pytest.mark.unit
class TestInitialization:
    @pytest.mark.parametrize("relativeKey", ALLOWED)
    def test_a_listed_extension_initializes(self, relativeKey):
        error, mock_s3 = _initialize(relativeKey)
        assert error is None or REFUSAL not in error, error
        mock_s3.create_multipart_upload.assert_called_once()

    @pytest.mark.parametrize("relativeKey", REFUSED)
    def test_another_name_is_refused_before_any_upload(self, relativeKey):
        error, mock_s3 = _initialize(relativeKey)
        assert error is not None and REFUSAL in error, error
        mock_s3.create_multipart_upload.assert_not_called()


@pytest.mark.unit
class TestUserCompletion:
    @pytest.mark.parametrize("relativeKey", ALLOWED)
    def test_a_listed_extension_is_copied(self, relativeKey):
        result, mock_copy = _complete(relativeKey)
        assert result.success is True, result.error
        mock_copy.assert_called_once()

    @pytest.mark.parametrize("relativeKey", REFUSED)
    def test_another_name_is_refused_and_not_copied(self, relativeKey):
        result, mock_copy = _complete(relativeKey)
        assert result.success is False
        assert result.error.startswith(REFUSAL), result.error
        mock_copy.assert_not_called()


@pytest.mark.unit
class TestExternalCompletion:
    @pytest.mark.parametrize("relativeKey", ALLOWED)
    def test_a_listed_extension_passes_the_check(self, relativeKey):
        assert _complete_external(relativeKey) == []

    @pytest.mark.parametrize("relativeKey", REFUSED)
    def test_another_name_is_refused(self, relativeKey):
        assert len(_complete_external(relativeKey)) == 1
