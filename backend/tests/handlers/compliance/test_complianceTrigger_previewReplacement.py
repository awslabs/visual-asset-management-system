# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A removal that a workflow execution's write of the same key supersedes starts no evaluation
(#1713).

The loop this guards: a pipeline rule's output is chained into the 3D Preview Thumbnail workflow,
whose write-back replaced the file's preview by deleting the key and copying the new version over
it. The delete marker carries no object metadata (a HEAD of it answers 405), so the trigger read the
removal as a user change and evaluated the asset, which launched the rule again. The upload
completion no longer deletes the key it overwrites; the trigger also skips such a removal, should
any writer delete and rewrite a key. A user's archive, whose delete marker stays current, is still
evaluated."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance.test_complianceTrigger import (
    ASSET, ASSET_ROWS, AUTO_EVAL_DB, DB, SCHEMA, _file_message, _run, _s3_head, _sns_event,
)
from handlers.compliance import complianceTrigger as trigger

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")
from backend.backend.handlers.assets import uploadFile  # noqa: E402,F401

PREVIEW = f"{ASSET}/rv1701.glb.previewFile.gif"
REMOVED_AT = "2026-03-01T12:00:00.573Z"
LAST_EVALUATED = {"schemaName": SCHEMA, "lastEvaluatedAt": "2026-03-01T11:58:00+00:00"}
DELETE_MARKER_HEAD = ClientError({"Error": {"Code": "405", "Message": "Method Not Allowed"}},
                                 "HeadObject")
CURRENT_IS_DELETE_MARKER = ClientError({"Error": {"Code": "404", "Message": "Not Found"}},
                                       "HeadObject")


def _removed(key, event_time=REMOVED_AT):
    message = _file_message(key=key, version_id="dm-1", event_time=event_time)
    message["eventName"] = "ObjectRemoved:DeleteMarkerCreated"
    return message


def _current(change_source, written_at):
    head = _s3_head(change_source)
    head["LastModified"] = written_at
    return head


def _run_removal(key, current_head):
    """The removal record through the handler: its versioned HEAD answers 405 (a delete marker), and
    the key's current version answers `current_head`."""
    return _run(_sns_event(_removed(key)), database_item=AUTO_EVAL_DB,
                compliance_record=LAST_EVALUATED, asset_rows=ASSET_ROWS,
                head=[DELETE_MARKER_HEAD, current_head])


@pytest.mark.unit
class TestASupersededRemovalIsTheWorkflowsWrite:
    def test_a_removal_rewritten_by_a_workflow_execution_is_not_evaluated(self):
        """The thumbnail write-back's delete marker is followed, milliseconds later in the same
        second, by the workflow's new version of the key (`LastModified` has no milliseconds)."""
        mocks = _run_removal(PREVIEW, _current("workflowExecution",
                                               datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)))
        mocks["run_evaluation"].assert_not_called()
        current_read = mocks["s3"].head_object.call_args_list[-1].kwargs
        assert current_read == {"Bucket": "asset-bucket", "Key": PREVIEW}

    def test_a_users_archive_is_evaluated(self):
        """A user's soft delete leaves the delete marker current: the key reads as not found."""
        mocks = _run_removal(f"{ASSET}/model.obj", CURRENT_IS_DELETE_MARKER)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_removal_rewritten_by_a_user_is_evaluated(self):
        mocks = _run_removal(PREVIEW, _current("upload", datetime(2026, 3, 1, 12, 0, 1,
                                                                 tzinfo=timezone.utc)))
        mocks["run_evaluation"].assert_called_once()

    def test_a_workflow_version_older_than_the_removal_does_not_supersede_it(self):
        """A version deleted under a current workflow write that predates the deletion (an old
        version removed by its version ID) is a change of its own."""
        mocks = _run_removal(PREVIEW, _current("workflowExecution",
                                               datetime(2026, 3, 1, 11, 59, 59, tzinfo=timezone.utc)))
        mocks["run_evaluation"].assert_called_once()

    def test_an_unreadable_current_version_is_evaluated(self):
        denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
        mocks = _run_removal(PREVIEW, denied)
        mocks["run_evaluation"].assert_called_once()

    def test_a_created_object_costs_no_second_read(self):
        mocks = _run(_sns_event(_file_message(key=PREVIEW)), database_item=AUTO_EVAL_DB,
                     compliance_record=LAST_EVALUATED, asset_rows=ASSET_ROWS, head=_s3_head())
        mocks["run_evaluation"].assert_called_once()
        mocks["s3"].head_object.assert_called_once()


@pytest.mark.unit
class TestOnAVersionedBucket:
    """The S3 behaviour the guard reads, on moto: a delete marker's HEAD is unreadable, and the
    key's current version names the writer that wrote it again."""

    @pytest.fixture
    def s3(self):
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="asset-bucket")
            client.put_bucket_versioning(Bucket="asset-bucket",
                                         VersioningConfiguration={"Status": "Enabled"})
            with patch.object(trigger, "s3_client", client):
                yield client

    def _removal_record(self, s3, key):
        (marker,) = s3.list_object_versions(Bucket="asset-bucket", Prefix=key)["DeleteMarkers"]
        removed_at = (marker["LastModified"] - timedelta(milliseconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
        record = _removed(key, event_time=removed_at)
        record["s3"]["object"]["versionId"] = marker["VersionId"]
        return record

    def test_a_delete_marker_has_no_readable_provenance(self, s3):
        s3.put_object(Bucket="asset-bucket", Key=PREVIEW, Body=b"gif",
                      Metadata={"vams-changesource": "workflowExecution"})
        s3.delete_object(Bucket="asset-bucket", Key=PREVIEW)
        record = self._removal_record(s3, PREVIEW)
        assert trigger.object_change_source(record["s3"], record) == ""
        assert trigger.removal_superseded_by_workflow_write(record, record["s3"], record) is False

    def test_a_workflow_rewrite_after_the_delete_marker_supersedes_it(self, s3):
        s3.put_object(Bucket="asset-bucket", Key=PREVIEW, Body=b"gif")
        s3.delete_object(Bucket="asset-bucket", Key=PREVIEW)
        record = self._removal_record(s3, PREVIEW)
        s3.put_object(Bucket="asset-bucket", Key=PREVIEW, Body=b"gif2",
                      Metadata={"vams-changesource": "workflowExecution"})
        assert trigger.removal_superseded_by_workflow_write(record, record["s3"], record) is True

    def test_the_preview_replacement_leaves_no_removal_to_evaluate(self, s3):
        """The completion's clean-up keeps the key it overwrites, so a same-extension replacement
        writes a new version and no delete marker: nothing reaches the trigger but the new
        version's own event, which carries `workflowExecution`."""
        from backend.backend.handlers.assets import uploadFile as uf
        s3.put_object(Bucket="asset-bucket", Key=PREVIEW, Body=b"gif",
                      Metadata={"vams-changesource": "workflowExecution"})
        with patch.object(uf, "s3", s3):
            assert uf.delete_existing_preview_files("asset-bucket", f"{ASSET}/rv1701.glb",
                                                    keep=PREVIEW) == []
        assert s3.list_object_versions(Bucket="asset-bucket", Prefix=ASSET).get("DeleteMarkers",
                                                                                []) == []
