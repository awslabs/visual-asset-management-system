# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The copy engine's five actions against moto's S3 and DynamoDB, with parts small enough for moto:
an 11 MiB staged object copied as three 5 MiB parts. Covers the end-to-end copy (metadata, content
headers, multipart object, staged object deleted, side effects once), resuming after a partial batch
(only the missing parts are copied), a source that changes mid-copy, completion retried after it
already happened, the failure cleanup per job kind, the streamed path for an in-VPC cross-Region
copy, and the exactly-once batch report."""

import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME", "t-copy-jobs")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "assetStorageTable")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-uploads")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-executions")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "sendEmailFunction")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "processOutput")

from backend.backend.handlers.assets import largeObjectCopy as engine  # noqa: E402

# The module the engine itself imported (`common.largeObjectCopy`), so its exception classes and the
# patched settings are the ones the engine sees.
loc = engine.loc

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
MIB = 1024 * 1024
SIZE = 11 * MIB
SMALL_PARTS = {"apiUploadEngineThresholdBytes": 5 * loc.GIB, "minPartSizeBytes": 5 * MIB,
               "partsPerBatch": 2, "threadsPerBatch": 4, "maxOutputIngestionWaitHours": 48,
               "batchesPerChunk": 1}
BODY = bytes(range(256)) * (SIZE // 256) + b"x" * (SIZE % 256)


@pytest.fixture
def env():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in ("run-bucket", "asset-bucket"):
            s3.create_bucket(Bucket=bucket)
            s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        s3.put_object(Bucket="run-bucket", Key="pipelines/p/j/output/e1/files/big.bin", Body=BODY,
                      ContentType="application/x-big", CacheControl="no-cache")
        ddb = boto3.resource("dynamodb", region_name=REGION)
        jobs = ddb.create_table(
            TableName="t-copy-jobs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"},
                                  {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
            GlobalSecondaryIndexes=[{
                "IndexName": loc.WORKFLOW_EXECUTION_ID_INDEX,
                "KeySchema": [{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}}],
            BillingMode="PAY_PER_REQUEST")
        assets = ddb.create_table(
            TableName="assetStorageTable",
            KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        assets.put_item(Item={"databaseId": "db", "assetId": "a1",
                              "assetLocation": {"Key": "a1/"}, "assetType": "none"})
        # The upload records table (empty: a test that tracks a file adds it with _uploads_table).
        uploads = ddb.create_table(
            TableName="t-uploads",
            KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        lambda_client = MagicMock()
        sfn_client = MagicMock()
        audit = MagicMock()
        with patch.object(engine, "s3", s3), \
                patch.object(engine, "_client_for_bucket", lambda bucket: s3), \
                patch.object(engine, "jobs_table", jobs), \
                patch.object(engine, "asset_table", assets), \
                patch.object(engine, "upload_table", uploads), \
                patch.object(engine, "dynamodb_client", boto3.client("dynamodb", region_name=REGION)), \
                patch.object(engine, "lambda_client", lambda_client), \
                patch.object(engine, "sfn_client", sfn_client), \
                patch.object(engine, "log_file_upload", audit), \
                patch.object(engine, "_streams_parts", lambda job: False), \
                patch.object(engine, "list_all_objects",
                             lambda bucket, prefix, client=None, max_objects=None:
                             s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])), \
                patch.object(engine, "is_object_version_archived", lambda *a, **k: False), \
                patch.object(loc, "settings", lambda environ=None: dict(SMALL_PARTS)):
            yield {"s3": s3, "jobs": jobs, "assets": assets, "lambda": lambda_client,
                   "sfn": sfn_client, "audit": audit}


def _job(kind=loc.KIND_WORKFLOW_OUTPUT, batch_id="e1"):
    job = loc.build_job(
        kind=kind,
        source={"bucket": "run-bucket", "key": "pipelines/p/j/output/e1/files/big.bin"},
        dest={"bucket": "asset-bucket", "key": "a1/big.bin"},
        metadata={"databaseid": "db", "assetid": "a1", "vams-changesource": "workflowExecution",
                  "vams-changeuserid": "u1", "vams-changeworkflowid": "wf",
                  "vams-changeworkflowexecutionid": "e1"},
        content_headers={}, database_id="db", asset_id="a1", upload_id="y1",
        upload_type="assetFile", relative_key="big.bin", change_user_id="u1", scope_id=batch_id,
        workflow_execution_id="e1", workflow_id="wf", workflow_database_id="GLOBAL",
        batch_id=batch_id if kind == loc.KIND_WORKFLOW_OUTPUT else "")
    loc.start_copy_job(engine.jobs_table, MagicMock(**{"start_execution.return_value": {"executionArn": "a"}}),
                       "arn:aws:states:r:1:stateMachine:sm", job)
    return job


def _run_copy(job):
    prepared = engine.prepare(job)
    for batch in prepared["batches"]:
        engine.copy_batch(prepared["job"], batch)
    return engine.complete(prepared["job"])["job"]


def test_copy_lands_the_object_with_final_metadata_and_headers_once(env):
    job = _run_copy(_job())
    s3 = env["s3"]
    head = s3.head_object(Bucket="asset-bucket", Key="a1/big.bin")
    assert head["ContentLength"] == SIZE
    assert head["ETag"].strip('"').endswith("-3")  # three planned parts
    assert head["Metadata"]["assetid"] == "a1"
    assert head["Metadata"]["vams-changeworkflowexecutionid"] == "e1"
    assert "uploadid" not in head["Metadata"]  # no provenance stamp carried over from staging
    assert head["ContentType"] == "application/x-big"
    assert head["CacheControl"] == "no-cache"
    assert s3.get_object(Bucket="asset-bucket", Key="a1/big.bin")["Body"].read() == BODY
    # The staged object's version is deleted, not hidden behind a delete marker.
    versions = s3.list_object_versions(Bucket="run-bucket", Prefix="pipelines/").get("Versions", [])
    assert versions == []
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_SUCCEEDED
    assert row["destVersionId"] == head["VersionId"]
    env["lambda"].invoke.assert_called_once()  # subscription email
    assert env["audit"].call_count == 1
    assert env["assets"].get_item(Key={"databaseId": "db", "assetId": "a1"})["Item"]["assetType"] == ".bin"


def test_create_multipart_upload_sets_the_bucket_owner_acl(env):
    real = env["s3"].create_multipart_upload
    with patch.object(env["s3"], "create_multipart_upload", side_effect=real) as create:
        engine.prepare(_job())
    assert create.call_args.kwargs["ACL"] == "bucket-owner-full-control"
    assert "MetadataDirective" not in create.call_args.kwargs


def test_a_retried_batch_copies_only_the_missing_parts(env):
    prepared = engine.prepare(_job())
    job = prepared["job"]
    engine._copy_part(job, 1)  # an earlier attempt copied part 1, then timed out
    real = env["s3"].upload_part_copy
    with patch.object(env["s3"], "upload_part_copy", side_effect=real) as copy:
        for batch in prepared["batches"]:
            engine.copy_batch(job, batch)
    assert sorted(c.kwargs["PartNumber"] for c in copy.call_args_list) == [2, 3]
    engine.complete(job)
    assert env["s3"].get_object(Bucket="asset-bucket", Key="a1/big.bin")["Body"].read() == BODY


def test_a_retried_prepare_reuses_the_recorded_upload(env):
    job = _job()
    first = engine.prepare(job)["job"]["uploadIdS3"]
    second = engine.prepare(job)["job"]["uploadIdS3"]
    assert first == second
    uploads = env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", [])
    assert len(uploads) == 1


def test_complete_after_an_earlier_completion_verifies_and_succeeds(env):
    prepared = engine.prepare(_job())
    for batch in prepared["batches"]:
        engine.copy_batch(prepared["job"], batch)
    engine.complete(prepared["job"])
    # The state is retried after its result was lost: the upload no longer exists.
    again = engine.complete(prepared["job"])["job"]
    assert again["destVersionId"]


def test_a_missing_part_fails_completion(env):
    prepared = engine.prepare(_job())
    engine.copy_batch(prepared["job"], prepared["batches"][0])
    with pytest.raises(RuntimeError):
        engine.complete(prepared["job"])


def test_a_source_that_vanishes_during_the_copy_is_not_retried(env):
    prepared = engine.prepare(_job())
    source = prepared["job"]["source"]
    env["s3"].delete_object(Bucket=source["bucket"], Key=source["key"], VersionId=source["versionId"])
    with pytest.raises(loc.SourceChanged):
        engine.copy_batch(prepared["job"], prepared["batches"][0])


def test_a_missing_source_fails_prepare_as_source_changed(env):
    env["s3"].delete_object(Bucket="run-bucket", Key="pipelines/p/j/output/e1/files/big.bin")
    with pytest.raises(loc.SourceChanged):
        engine.prepare(_job())


def test_a_blocked_type_is_refused_before_anything_lands(env):
    with patch.object(engine, "validateUnallowedFileExtensionAndContentType", lambda key, ct: False):
        with pytest.raises(loc.CopyRefused):
            engine.prepare(_job())
    assert env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", []) == []


def test_a_preview_is_refused(env):
    job = {**_job(), "relativeKey": "big.bin.previewFile.png"}
    with pytest.raises(loc.CopyRefused):
        engine.prepare(job)


def test_a_cancelled_job_does_no_work(env):
    job = _job()
    loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_CANCELLED})
    with pytest.raises(loc.CopyRefused):
        engine.prepare(job)


def test_fail_aborts_the_upload_and_keeps_a_workflow_output_staged(env):
    prepared = engine.prepare(_job())
    engine.fail(prepared["job"], {"Error": "States.TaskFailed", "Cause": "{}"})
    assert env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", []) == []
    env["s3"].head_object(Bucket="run-bucket", Key="pipelines/p/j/output/e1/files/big.bin")
    row = loc.get_job_row(env["jobs"], prepared["job"]["jobId"])
    assert row["status"] == loc.STATUS_FAILED
    assert row["error"] == loc.REASON_COPY_FAILED
    assert env["audit"].call_args.args[4] is True  # denied entry


def test_fail_of_an_upload_deletes_its_staged_object(env):
    prepared = engine.prepare(_job(kind=loc.KIND_UPLOAD))
    engine.fail(prepared["job"], {"Error": "SourceChanged", "Cause": "{}"})
    versions = env["s3"].list_object_versions(Bucket="run-bucket", Prefix="pipelines/").get("Versions", [])
    assert versions == []
    assert loc.get_job_row(env["jobs"], prepared["job"]["jobId"])["error"] == loc.REASON_SOURCE_CHANGED


def test_fail_keeps_a_cancelled_status(env):
    job = _job()
    loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_CANCELLED})
    engine.fail(job, {"Error": "States.TaskFailed"})
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_CANCELLED


@pytest.mark.parametrize("error,reason", [
    ({"Error": "SourceChanged"}, loc.REASON_SOURCE_CHANGED),
    ({"Error": "CopyRefused", "Cause": json.dumps({"errorMessage": loc.REASON_TOO_LARGE})}, loc.REASON_TOO_LARGE),
    ({"Error": "CopyRefused", "Cause": json.dumps({"errorMessage": loc.REASON_BLOCKED})}, loc.REASON_BLOCKED),
    ({"Error": "States.Timeout"}, loc.REASON_COPY_FAILED),
    ({}, loc.REASON_COPY_FAILED),
])
def test_failure_reasons_are_generic(error, reason):
    assert engine._failure_reason(error) == reason


def test_streamed_copy_for_an_in_vpc_cross_region_job(env):
    with patch.object(engine, "_streams_parts", lambda job: True):
        real = env["s3"].upload_part
        with patch.object(env["s3"], "upload_part", side_effect=real) as upload:
            _run_copy(_job())
    assert upload.call_count == 3
    assert env["s3"].get_object(Bucket="asset-bucket", Key="a1/big.bin")["Body"].read() == BODY


def test_notify_reports_a_waiting_workflow_once(env):
    job = _run_copy(_job())
    loc.record_batch(env["jobs"], "e1", [job["jobId"]], {"workflowExecutionId": "e1", "legacy": False})
    loc.register_task_token(env["jobs"], "e1", "token-1")
    engine.notify(job, loc.STATUS_SUCCEEDED)
    engine.notify(job, loc.STATUS_SUCCEEDED)  # a retried notify
    env["sfn"].send_task_success.assert_called_once()
    assert env["sfn"].send_task_success.call_args.kwargs["taskToken"] == "token-1"


def test_notify_of_a_legacy_batch_invokes_finalize(env):
    job = _run_copy(_job())
    env["lambda"].reset_mock()
    loc.record_batch(env["jobs"], "e1", [job["jobId"]], {"workflowExecutionId": "e1", "legacy": True})
    engine.notify(job, loc.STATUS_SUCCEEDED)
    kwargs = env["lambda"].invoke.call_args.kwargs
    # The name the engine resolved at import (another test module may set the variable first).
    assert kwargs["FunctionName"] == engine.process_output_function_name
    assert json.loads(kwargs["Payload"])["body"]["mode"] == "finalize"


def test_notify_of_an_upload_job_reports_nothing(env):
    job = _run_copy(_job(kind=loc.KIND_UPLOAD))
    env["lambda"].reset_mock()
    assert engine.notify(job, loc.STATUS_SUCCEEDED) == {"status": loc.STATUS_SUCCEEDED}
    env["sfn"].send_task_success.assert_not_called()
    env["lambda"].invoke.assert_not_called()


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        engine.lambda_handler({"action": "delete", "job": {"jobId": "x"}}, None)


def test_each_copied_batch_counts_its_parts_once(env):
    """Display-only part progress: a retried or resumed batch is not counted twice."""
    job = _job()
    prepared = engine.prepare(job)
    batches = prepared["batches"]
    engine.copy_batch(prepared["job"], batches[0])
    engine.copy_batch(prepared["job"], batches[0])  # a retried batch copies nothing new
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert int(row["partsCopied"]) == batches[0]["endPart"] - batches[0]["startPart"] + 1
    for batch in batches[1:]:
        engine.copy_batch(prepared["job"], batch)
    assert int(loc.get_job_row(env["jobs"], job["jobId"])["partsCopied"]) == prepared["job"]["partCount"]


def test_a_progress_write_failure_does_not_fail_the_batch(env):
    job = _job()
    prepared = engine.prepare(job)
    with patch.object(engine.jobs_table, "update_item", side_effect=RuntimeError("ddb down")):
        assert engine.copy_batch(prepared["job"], prepared["batches"][0])["copied"] > 0


def test_a_cancel_during_prepare_aborts_the_new_upload(env):
    """A cancel that read the job row before Prepare recorded its upload cannot abort that upload;
    Prepare does not record it over the CANCELLED status, aborts it and refuses the copy."""
    job = _job()
    real_create = env["s3"].create_multipart_upload

    def create_then_cancel(**kwargs):
        created = real_create(**kwargs)
        loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_CANCELLED})
        return created

    with patch.object(env["s3"], "create_multipart_upload", side_effect=create_then_cancel):
        with pytest.raises(loc.CopyRefused):
            engine.prepare(job)
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_CANCELLED
    assert "uploadIdS3" not in row
    assert env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", []) == []


def _uploads_table():
    """The fixture's upload records table, now tracking big.bin of upload y1 as PENDING."""
    table = boto3.resource("dynamodb", region_name=REGION).Table("t-uploads")
    table.put_item(Item={"uploadId": "y1", "assetId": "a1", "databaseId": "db", "status": "processing_async",
                         "asyncFiles": {"big.bin": {"status": "PENDING"}}})
    return table


@pytest.mark.parametrize("succeeded", [True, False])
def test_an_api_upload_copy_records_its_outcome_on_the_upload(env, succeeded):
    """M6: before, a failed engine copy of an API upload over 5 GiB was recorded only in the audit
    log; the uploader now reads it from the upload record."""
    uploads = _uploads_table()
    with patch.object(engine, "upload_table", uploads):
        job = _job(kind=loc.KIND_UPLOAD)
        if succeeded:
            _run_copy(job)
        else:
            engine.fail(job, {"Error": "SourceChanged", "Cause": ""})
    record = uploads.get_item(Key={"uploadId": "y1", "assetId": "a1"})["Item"]
    entry = record["asyncFiles"]["big.bin"]
    assert entry["status"] == ("SUCCEEDED" if succeeded else "FAILED")
    assert record["status"] == ("completed" if succeeded else "completed_with_errors")
    if not succeeded:
        assert entry["error"] == loc.REASON_SOURCE_CHANGED


def test_a_workflow_output_copy_does_not_touch_upload_records(env):
    uploads = _uploads_table()
    with patch.object(engine, "upload_table", uploads):
        _run_copy(_job())
    assert uploads.get_item(Key={"uploadId": "y1", "assetId": "a1"})["Item"]["asyncFiles"]["big.bin"]["status"] == "PENDING"
