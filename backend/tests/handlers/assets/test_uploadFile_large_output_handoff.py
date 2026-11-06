# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow outputs over the async threshold on the external completion route (#1123).

When the caller names an ingestion batch, each file over 1 GiB is handed to a large object copy job:
the result carries the job id, the queue is not used, no synchronous copy runs here (the old
fallback copied a terabyte file inside this function), and a job that cannot be started fails that
file. Files at or under the threshold, and calls that name no batch, are unchanged. A run whose only
outputs are large is no longer answered "No files were successfully uploaded" (#1140: every
large-only write-back was recorded FAILED while the copies landed)."""

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
EXEC = "0123456789abcdef0123456789abcdef"
TEMP_PREFIX = f"pipelines/p/j/output/{EXEC}/files/"
SIZES = {"small.glb": 1024, "big.e57": 3 * GIB, "edge.bin": GIB}


def _request(*keys, batch=EXEC, workflow_database_id="GLOBAL"):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId="asset-1", databaseId="db-1", uploadType="assetFile",
        files=[{"relativeKey": k, "tempKey": TEMP_PREFIX + k} for k in keys],
        workflowId="wf1", workflowExecutionId=EXEC, changeUserId="user1",
        sourceBucket="run-bucket", ingestionBatchId=batch,
        workflowDatabaseId=workflow_database_id)


@contextmanager
def _env(start_side_effect=None):
    from backend.backend.handlers.assets import uploadFile as uf
    with patch.object(uf, "get_upload_details", return_value={
        "assetId": "asset-1", "databaseId": "db-1", "uploadType": "assetFile",
        "isExternalUpload": True, "temporaryPrefix": TEMP_PREFIX}), \
            patch.object(uf, "get_asset_details", return_value={
                "assetId": "asset-1", "databaseId": "db-1", "bucketId": "b1",
                "assetLocation": {"Key": "asset-1/"}}), \
            patch.object(uf, "get_default_bucket_details", return_value={
                "bucketId": "b1", "bucketName": "asset-bucket", "baseAssetsPrefix": "",
                "bucketRegion": "us-east-1"}), \
            patch.object(uf, "bucket_region_for_name", return_value="us-east-1"), \
            patch.object(uf, "get_database_details", return_value={"databaseId": "db-1"}), \
            patch.object(uf, "asset_upload_table", MagicMock()), \
            patch.object(uf, "delete_upload_details", MagicMock()), \
            patch.object(uf, "validateS3AssetExtensionsAndContentType", return_value=True), \
            patch.object(uf, "determine_asset_type", return_value=".glb"), \
            patch.object(uf, "send_subscription_email", MagicMock()) as email, \
            patch.object(uf, "log_file_upload", MagicMock()), \
            patch.object(uf, "s3", MagicMock()) as s3, \
            patch.object(uf, "copy_s3_object", return_value=True) as copy, \
            patch.object(uf, "delete_s3_object") as delete, \
            patch.object(uf, "queue_large_file_for_processing", return_value=True) as queue, \
            patch.object(uf, "update_asset_attributes") as update_asset, \
            patch.object(uf, "copy_jobs_table", return_value=MagicMock()), \
            patch.object(uf.loc, "start_copy_job", side_effect=start_side_effect) as start:
        s3.head_object.side_effect = lambda Bucket, Key: {
            "ContentLength": SIZES[Key[len(TEMP_PREFIX):]], "VersionId": "v1", "ETag": '"e"'}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        yield uf, {"copy": copy, "queue": queue, "start": start, "delete": delete,
                   "update_asset": update_asset, "email": email}


def _result(response, key):
    return next(r for r in response.fileResults if r.relativeKey == key)


@pytest.mark.unit
class TestLargeOutputHandOff:
    def test_a_large_output_is_handed_to_a_copy_job(self):
        with _env() as (uf, m):
            response = uf.complete_external_upload("up-1", _request("small.glb", "big.e57"), {})
        big = _result(response, "big.e57")
        assert big.success is True and big.largeFileAsynchronousHandling is True
        assert big.ingestionJobId.startswith(EXEC + "#")
        m["queue"].assert_not_called()
        # Only the small file is copied here.
        assert [c.args[1] for c in m["copy"].call_args_list] == [TEMP_PREFIX + "small.glb"]
        assert response.overallSuccess is True

    def test_the_job_carries_the_final_metadata_and_the_pinned_source(self):
        with _env() as (uf, m):
            uf.complete_external_upload("up-1", _request("big.e57"), {})
        job = m["start"].call_args.args[3]
        assert job["kind"] == "workflowOutput" and job["batchId"] == EXEC
        assert job["source"] == {"bucket": "run-bucket", "key": TEMP_PREFIX + "big.e57",
                                 "region": "us-east-1", "versionId": "v1", "etag": '"e"',
                                 "size": 3 * GIB}
        assert job["dest"]["key"] == "asset-1/big.e57"
        assert job["metadata"]["assetid"] == "asset-1"
        assert job["metadata"]["vams-changeworkflowexecutionid"] == EXEC
        # Every shipped pipeline registers a GLOBAL workflow (#1123 D2).
        assert job["workflowDatabaseId"] == "GLOBAL"
        assert job["deleteSourceOnSuccess"] is True

    def test_a_job_that_cannot_start_fails_the_file_without_a_synchronous_copy(self):
        with _env(start_side_effect=RuntimeError("no engine")) as (uf, m):
            response = uf.complete_external_upload("up-1", _request("small.glb", "big.e57"), {})
        big = _result(response, "big.e57")
        assert big.success is False
        assert big.error == uf.loc.REASON_START_FAILED
        assert response.overallSuccess is False
        assert TEMP_PREFIX + "big.e57" not in [c.args[1] for c in m["copy"].call_args_list]

    def test_a_large_only_output_is_accepted(self):
        with _env() as (uf, m):
            response = uf.complete_external_upload("up-1", _request("big.e57"), {})
        assert response.overallSuccess is True
        assert response.message != "No files were successfully uploaded"
        m["copy"].assert_not_called()
        # The asset type and the subscription email follow the file's landing, in the engine.
        m["email"].assert_not_called()
        m["update_asset"].assert_not_called()

    def test_a_file_at_the_threshold_keeps_the_synchronous_path(self):
        with _env() as (uf, m):
            response = uf.complete_external_upload("up-1", _request("edge.bin"), {})
        assert _result(response, "edge.bin").ingestionJobId is None
        m["start"].assert_not_called()
        m["copy"].assert_called_once()

    def test_without_a_batch_the_queue_path_is_unchanged(self):
        with _env() as (uf, m):
            response = uf.complete_external_upload("up-1", _request("big.e57", batch=None), {})
        m["start"].assert_not_called()
        m["queue"].assert_called_once()
        assert _result(response, "big.e57").ingestionJobId is None


@pytest.mark.unit
class TestIngestionBatchValidation:
    def test_the_batch_must_be_the_run_itself(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _request("big.e57", batch="f" * 32)

    def test_the_workflow_database_id_is_validated(self):
        from pydantic import ValidationError
        from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
        with pytest.raises(ValidationError):
            CompleteExternalUploadRequestModel(
                assetId="asset-1", databaseId="db-1", uploadType="assetFile",
                files=[{"relativeKey": "a", "tempKey": TEMP_PREFIX + "a"}],
                workflowExecutionId=EXEC, ingestionBatchId=EXEC, workflowDatabaseId="bad id!")

    @pytest.mark.parametrize("workflow_database_id", ["GLOBAL", "wfdb-1"])
    def test_a_global_or_database_workflow_is_accepted(self, workflow_database_id):
        # D2: the GLOBAL keyword was refused, so every GLOBAL workflow's write-back failed.
        request = _request("big.e57", workflow_database_id=workflow_database_id)
        assert request.workflowDatabaseId == workflow_database_id

    @pytest.mark.parametrize("workflow_database_id", ["global", "Global"])
    def test_the_global_keyword_must_be_capitalized(self, workflow_database_id):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _request("big.e57", workflow_database_id=workflow_database_id)

    def test_a_database_workflow_reaches_its_job(self):
        with _env() as (uf, m):
            uf.complete_external_upload("up-1", _request("big.e57", workflow_database_id="wfdb-1"), {})
        assert m["start"].call_args.args[3]["workflowDatabaseId"] == "wfdb-1"
