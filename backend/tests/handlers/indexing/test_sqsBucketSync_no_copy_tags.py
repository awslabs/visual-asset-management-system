# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync never rewrites a directly written object: no copy and no object write. Each attributed
version gets the two VAMS tags instead (unless its registration has `tagSyncedObjects` off), and its
ledger row records the outcome. The echo of a metadata stamp copy an earlier release made is still
dropped."""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: F401
    BUCKET,
    _history_rows,
    _restore_module_attrs,
    _record,
    _row,
    _sqs,
    _wire,
)
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# Restored here beyond what the location-attribution suite's autouse fixture restores.
_RESTORED = ("get_bucket_info_from_bucket_id", "self_function_name")
_REAL = {}


@pytest.fixture(autouse=True)
def _restore(_restore_module_attrs):  # noqa: F811
    m = _load()
    saved = {name: getattr(m, name) for name in _RESTORED}
    _REAL["tag_version"] = _restore_module_attrs["tag_version"]
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _wire_real_tags(m, registration=None, tag_set=None):
    """The location-attribution wiring with the real `tag_version` and an S3 client that records every
    tagging call."""
    _wire(m, live=_row())
    m.tag_version = _REAL["tag_version"]
    m.get_bucket_info_from_bucket_id = MagicMock(return_value=registration if registration is not None else {
        "bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "/"})
    m.s3_client.get_object_tagging.return_value = {"TagSet": list(tag_set or [])}


def _object_writes(m):
    """Every S3 call that would write object data: copies, puts, uploads and multipart parts."""
    names = ("copy_object", "copy", "put_object", "upload_file", "upload_fileobj", "create_multipart_upload",
             "upload_part_copy", "complete_multipart_upload")
    return [name for name in names if getattr(m.s3_client, name).called]


@pytest.mark.unit
class TestNoObjectWrite:
    def test_a_direct_write_is_never_copied_or_rewritten(self):
        m = _load()
        _wire_real_tags(m)
        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/model.glb", "v1"))]},
                                            MagicMock())
        assert response["batchItemFailures"] == []
        assert _object_writes(m) == []
        assert not hasattr(m, "update_s3_metadata")
        assert not hasattr(m, "s3_resource")

    def test_the_echo_of_an_earlier_stamp_copy_is_still_dropped(self):
        m = _load()
        _wire_real_tags(m)
        m.self_function_name = "bucket-sync-fn"
        record = dict(_record("a1/model.glb", "v2", event_name="ObjectCreated:Copy"),
                      userIdentity={"principalId": "AWS:AROAEXAMPLE:bucket-sync-fn"})
        assert m.process_s3_record(record)[:2] == (True, False)
        m.s3_client.get_object_tagging.assert_not_called()
        assert _history_rows(m) == []


@pytest.mark.unit
class TestTagsOnSyncedVersions:
    def test_an_attributed_version_is_tagged_and_the_ledger_says_so(self):
        m = _load()
        _wire_real_tags(m, tag_set=[{"Key": "team", "Value": "x"}])
        m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/model.glb", "v1"))]}, MagicMock())
        m.s3_client.get_object_tagging.assert_called_once_with(Bucket=BUCKET, Key="a1/model.glb", VersionId="v1")
        written = m.s3_client.put_object_tagging.call_args.kwargs
        assert written["VersionId"] == "v1"
        assert written["Tagging"]["TagSet"] == [
            {"Key": "team", "Value": "x"}, {"Key": "VAMS:databaseId", "Value": "db1"},
            {"Key": "VAMS:assetId", "Value": "a1"}]
        [row] = _history_rows(m)
        assert row["vamsTags"] == "written"
        assert "vamsTagsReason" not in row

    def test_a_denied_tag_write_still_attributes_indexes_and_records(self):
        m = _load()
        _wire_real_tags(m)
        m.s3_client.get_object_tagging.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObjectTagging")
        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/model.glb", "v1"))]},
                                            MagicMock())
        assert response["batchItemFailures"] == []
        [row] = _history_rows(m)
        assert (row["vamsTags"], row["vamsTagsReason"]) == ("skipped", "accessDenied")
        assert m.sns_client.publish.called and m.events_client.put_events.called

    def test_tagging_off_on_the_registration_makes_no_tag_call(self):
        m = _load()
        _wire_real_tags(m, registration={"bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "/",
                                         "tagSyncedObjects": False})
        m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/model.glb", "v1"))]}, MagicMock())
        m.s3_client.get_object_tagging.assert_not_called()
        m.s3_client.put_object_tagging.assert_not_called()
        [row] = _history_rows(m)
        assert "vamsTags" not in row

    def test_a_registration_without_the_switch_is_tagged(self):
        m = _load()
        _wire_real_tags(m, registration={"bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/model.glb", "v1"))]}, MagicMock())
        assert m.s3_client.put_object_tagging.called

    def test_a_folder_marker_is_not_tagged(self):
        m = _load()
        _wire_real_tags(m)
        m.lambda_handler_created({"Records": [_sqs("msg-1", _record("a1/sub/"))]}, MagicMock())
        m.s3_client.get_object_tagging.assert_not_called()


def test_records_used_here_are_plain_json():
    # The SQS bodies these tests build are what the deployed queue delivers.
    assert json.loads(_sqs("m", _record("a1/x.glb"))["body"])["Type"] == "Notification"
