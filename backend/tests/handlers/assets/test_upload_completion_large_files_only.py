# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A completion whose every file is over the async threshold is accepted, not failed (#1140).

Files over 1 GiB are queued for the large-file consumer and never enter the list of files the
completion copies itself. Both completion routes treated an empty list as "No files were
successfully uploaded" (overallSuccess False, so the CLI exited 1 and a workflow write-back of only
large outputs ended FAILED) although every queued file then landed.

Pinned here, for both routes:
- a large-only completion answers overallSuccess True with the files flagged asynchronous;
- the asset type and the subscription email are left to the consumer (the file has not landed);
- the 1 GiB boundary: exactly 1 GiB is copied synchronously, one byte more is queued;
- mixed and small-only completions, and a large file that could not be queued, behave as before.
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

GIB = 1024 ** 3
NO_FILES = "No files were successfully uploaded"
UPLOAD_ID = "up-1"
TEMP_PREFIX = "temp/up-1/"
SIZES = {
    "/big.e57": 3 * GIB,
    "/huge.las": 2 * GIB,
    # The single-file case from the issue report: 1,269,109,728 bytes.
    "/archive.zip": 1_269_109_728,
    "/edge.bin": GIB,
    "/over.bin": GIB + 1,
    "/small.glb": 1024,
}


def _asset():
    return {"assetId": "asset-1", "databaseId": "db-1", "bucketId": "b1",
            "assetLocation": {"Key": "asset-1/"}}


@contextmanager
def _env(upload_record):
    """Both completion routes with S3, the queue and the asset writes stubbed. The multipart size
    comes from SIZES by the file's temp key; the external HEAD answers the same sizes."""
    from backend.backend.handlers.assets import uploadFile as uf

    def _size_from_parts(bucket, key, upload_id, parts):
        return SIZES["/" + key.rsplit("/", 1)[-1]], True, None

    def _head(Bucket, Key, **_):
        name = "/" + Key.rsplit("/", 1)[-1]
        return {"ContentLength": SIZES.get(name, 1024), "VersionId": "v1", "ETag": '"e"',
                "Metadata": {uf.UPLOAD_ID_METADATA_KEY: UPLOAD_ID}}

    with patch.object(uf, "get_upload_details", return_value=upload_record), \
            patch.object(uf, "get_asset_details", return_value=_asset()), \
            patch.object(uf, "get_default_bucket_details", return_value={
                "bucketId": "b1", "bucketName": "asset-bucket", "baseAssetsPrefix": "",
                "bucketRegion": "us-east-1"}), \
            patch.object(uf, "bucket_region_for_name", return_value="us-east-1"), \
            patch.object(uf, "get_database_details", return_value={"databaseId": "db-1"}), \
            patch.object(uf, "asset_upload_table", MagicMock()), \
            patch.object(uf, "asset_table", MagicMock()), \
            patch.object(uf, "delete_upload_details", MagicMock()), \
            patch.object(uf, "log_file_upload", MagicMock()), \
            patch.object(uf, "validateUnallowedFileExtensionAndContentType", return_value=True), \
            patch.object(uf, "validateS3AssetExtensionsAndContentType", return_value=True), \
            patch.object(uf, "calculate_total_file_size_from_parts", side_effect=_size_from_parts), \
            patch.object(uf, "queue_large_file_for_processing", return_value=True) as queue, \
            patch.object(uf, "copy_s3_object", return_value=True) as copy, \
            patch.object(uf, "delete_s3_object", MagicMock()), \
            patch.object(uf, "determine_asset_type", return_value=".glb"), \
            patch.object(uf, "update_asset_attributes") as update_asset, \
            patch.object(uf, "send_subscription_email", MagicMock()) as email, \
            patch.object(uf, "claims_and_roles", {"tokens": ["alice@corp"]}, create=True), \
            patch.object(uf, "s3", MagicMock()) as s3:
        s3.head_object.side_effect = _head
        yield uf, {"queue": queue, "copy": copy, "update_asset": update_asset, "email": email}


def _user_record(keys):
    return {"uploadId": UPLOAD_ID, "assetId": "asset-1", "databaseId": "db-1",
            "uploadType": "assetFile", "isExternalUpload": False,
            "expiresAt": 4102444800, "files": list(keys)}


def _user_request(*keys):
    from backend.backend.models.assetsV3 import CompleteUploadRequestModel
    return CompleteUploadRequestModel(
        assetId="asset-1", databaseId="db-1", uploadType="assetFile",
        files=[{"relativeKey": k, "uploadIdS3": "s3-" + k.strip("/"),
                "parts": [{"PartNumber": 1, "ETag": "etag-1"}]} for k in keys])


def _complete_user(*keys):
    with _env(_user_record(keys)) as (uf, m):
        response = uf.complete_upload(UPLOAD_ID, _user_request(*keys), {})
    return response, m


def _external_record():
    return {"assetId": "asset-1", "databaseId": "db-1", "uploadType": "assetFile",
            "isExternalUpload": True, "temporaryPrefix": TEMP_PREFIX}


def _external_request(*keys):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId="asset-1", databaseId="db-1", uploadType="assetFile",
        files=[{"relativeKey": k, "tempKey": TEMP_PREFIX + k.lstrip("/")} for k in keys],
        workflowId="wf1", workflowExecutionId="0123456789abcdef0123456789abcdef",
        changeUserId="user1")


def _complete_external(*keys):
    with _env(_external_record()) as (uf, m):
        response = uf.complete_external_upload(UPLOAD_ID, _external_request(*keys), {})
    return response, m


@pytest.mark.unit
class TestUserUploadOfLargeFilesOnly:
    def test_a_single_large_file_is_accepted(self):
        response, m = _complete_user("/archive.zip")
        assert response.message != NO_FILES
        assert response.overallSuccess is True
        assert response.largeFileAsynchronousHandling is True
        assert [(r.success, r.largeFileAsynchronousHandling) for r in response.fileResults] == [(True, True)]
        m["queue"].assert_called_once()
        m["copy"].assert_not_called()

    def test_several_large_files_are_accepted(self):
        response, m = _complete_user("/big.e57", "/huge.las")
        assert response.overallSuccess is True
        assert m["queue"].call_count == 2

    def test_the_asset_update_waits_for_the_file_to_land(self):
        _response, m = _complete_user("/big.e57")
        # The large-file consumer sets the asset type and notifies subscribers once it lands.
        m["update_asset"].assert_not_called()
        m["email"].assert_not_called()

    def test_exactly_one_gib_is_copied_synchronously(self):
        response, m = _complete_user("/edge.bin")
        assert response.overallSuccess is True
        assert response.largeFileAsynchronousHandling is False
        m["queue"].assert_not_called()
        m["copy"].assert_called_once()

    def test_one_byte_over_one_gib_is_queued_and_accepted(self):
        response, m = _complete_user("/over.bin")
        assert response.overallSuccess is True
        assert response.largeFileAsynchronousHandling is True
        m["queue"].assert_called_once()
        m["copy"].assert_not_called()

    def test_a_mixed_upload_is_unchanged(self):
        response, m = _complete_user("/small.glb", "/big.e57")
        assert response.overallSuccess is True
        m["copy"].assert_called_once()
        m["update_asset"].assert_called_once()
        m["email"].assert_called_once()


@pytest.mark.unit
class TestExternalCompletionOfLargeFilesOnly:
    def test_a_large_only_completion_is_accepted(self):
        response, m = _complete_external("/big.e57")
        assert response.message != NO_FILES
        assert response.overallSuccess is True
        assert response.largeFileAsynchronousHandling is True
        m["queue"].assert_called_once()
        m["copy"].assert_not_called()
        m["update_asset"].assert_not_called()
        m["email"].assert_not_called()

    def test_one_byte_over_one_gib_is_queued_and_accepted(self):
        response, m = _complete_external("/over.bin")
        assert response.overallSuccess is True
        m["queue"].assert_called_once()

    def test_exactly_one_gib_is_copied_synchronously(self):
        response, m = _complete_external("/edge.bin")
        assert response.overallSuccess is True
        assert response.largeFileAsynchronousHandling is False
        m["queue"].assert_not_called()
        m["copy"].assert_called_once()

    def test_a_small_only_completion_is_unchanged(self):
        response, m = _complete_external("/small.glb")
        assert response.overallSuccess is True
        m["copy"].assert_called_once()
        m["update_asset"].assert_called_once()

    def test_a_queue_failure_still_falls_back_and_fails_honestly(self):
        with _env(_external_record()) as (uf, m):
            m["queue"].return_value = False
            m["copy"].return_value = False
            response = uf.complete_external_upload(UPLOAD_ID, _external_request("/big.e57"), {})
        # Not queued, and the synchronous fallback copy failed: nothing was accepted, so the
        # completion still answers 409 (every file failed).
        assert response["statusCode"] == 409
