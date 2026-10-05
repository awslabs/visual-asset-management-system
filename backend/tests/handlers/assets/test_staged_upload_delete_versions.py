# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The staged upload object under temp-uploads/ is removed, not hidden.

Asset buckets are versioned, so a DeleteObject without a version ID only stacks a delete marker on
the staged copy and keeps it as a noncurrent version: every upload left a full hidden copy of the
file. Both the uploadFile and the large-file (sqsUploadFileLarge) helpers delete the staged
object's current version by its ID instead.
"""

import os
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import sqsUploadFileLarge as large  # noqa: E402
from backend.backend.handlers.assets import uploadFile as upload  # noqa: E402

BUCKET = "asset-bucket"
KEY = "temp-uploads/db1/asset1/model.glb"


def _client_error(code, op="DeleteObject"):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


@pytest.fixture(params=[upload, large], ids=["uploadFile", "sqsUploadFileLarge"])
def mod_s3(request, monkeypatch):
    s3 = MagicMock(name="s3")
    monkeypatch.setattr(request.param, "s3", s3)
    return request.param, s3


def test_versioned_staged_object_is_deleted_by_its_version_id(mod_s3):
    mod, s3 = mod_s3
    s3.head_object.return_value = {"VersionId": "v-staged"}

    assert mod.delete_s3_object(BUCKET, KEY) is True

    s3.delete_object.assert_called_once_with(Bucket=BUCKET, Key=KEY, VersionId="v-staged")


def test_key_without_a_current_version_issues_no_delete(mod_s3):
    """A plain delete there would stack another delete marker on nothing."""
    mod, s3 = mod_s3
    s3.head_object.side_effect = _client_error("404", "HeadObject")

    assert mod.delete_s3_object(BUCKET, KEY) is True

    s3.delete_object.assert_not_called()


def test_unversioned_bucket_uses_a_plain_delete(mod_s3):
    mod, s3 = mod_s3
    s3.head_object.return_value = {"ContentLength": 3}

    assert mod.delete_s3_object(BUCKET, KEY) is True

    s3.delete_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


def test_refused_version_delete_falls_back_to_a_plain_delete(mod_s3):
    """An external bucket policy scoped below s3:DeleteObjectVersion keeps the earlier behavior."""
    mod, s3 = mod_s3
    s3.head_object.return_value = {"VersionId": "v-staged"}
    s3.delete_object.side_effect = [_client_error("AccessDenied"), {}]

    assert mod.delete_s3_object(BUCKET, KEY) is True

    assert s3.delete_object.call_args_list[0].kwargs == {"Bucket": BUCKET, "Key": KEY, "VersionId": "v-staged"}
    assert s3.delete_object.call_args_list[1].kwargs == {"Bucket": BUCKET, "Key": KEY}


def test_other_delete_errors_report_failure(mod_s3):
    mod, s3 = mod_s3
    s3.head_object.return_value = {"VersionId": "v-staged"}
    s3.delete_object.side_effect = _client_error("InternalError")

    assert mod.delete_s3_object(BUCKET, KEY) is False

    s3.delete_object.assert_called_once()
