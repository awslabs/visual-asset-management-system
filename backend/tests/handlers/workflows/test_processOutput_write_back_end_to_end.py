# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The output step's write-back, end to end through the real file-ingestion handler (#1123 D2).

The other output-step tests stub the file-ingestion cross-call, and the ingestion tests build the
request model by hand, so neither sees the body the output step actually sends being parsed by the
model the ingestion Lambda actually uses. That gap let a GLOBAL workflowDatabaseId through review:
every shipped pipeline registers a GLOBAL workflow, and the model refused the keyword, so every
GLOBAL workflow's write-back failed after the upgrade.

Here the output step's own process_external_upload builds the cross-call and the uploadFile Lambda
handler parses, authorizes and completes it (S3, DynamoDB and Casbin stubbed). Each case runs for
a GLOBAL workflow and a database-scoped one, with only small outputs, an output over 1 GiB (the
copy threshold) and an output over 5 GiB."""

import json
import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "test-s3-buckets-table",
    "ASSET_UPLOAD_TABLE_NAME": "test-asset-upload-table",
    "SEND_EMAIL_FUNCTION_NAME": "test-send-email-function",
    "PRESIGNED_URL_TIMEOUT_SECONDS": "3600",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.copyEngineRouting import route_to_engine  # noqa: E402
from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402
from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402

po = base.po
GIB = 1024 ** 3
# A real execution id: the ingestion model validates workflowExecutionId as a GUID, and the
# ingestion batch must equal it.
EXEC = "0123456789abcdef0123456789abcdef"
# The output asset's database (the ingestion model requires at least 4 characters).
DB = "dbase-1"
FILES_PREFIX = f"pipelines/p1/JOB/output/{EXEC}/files/"

SMALL = {"Key": FILES_PREFIX + "small.glb", "Size": 7}
OVER_1_GIB = {"Key": FILES_PREFIX + "big/cloud.e57", "Size": int(1.5 * GIB)}
OVER_5_GIB = {"Key": FILES_PREFIX + "huge/scan.las", "Size": 6 * GIB}

LISTINGS = {
    "small-only": [SMALL],
    "over-1-gib": [SMALL, OVER_1_GIB],
    "over-5-gib": [SMALL, OVER_5_GIB],
}
WORKFLOW_DATABASES = ["GLOBAL", "wfdb-1"]


def _run(workflow_database_id, objects):
    """Run the output step's ingest mode over `objects`, with the file-ingestion cross-call served
    by the real uploadFile handler. Returns (step response, ingestion responses, mocks)."""
    sizes = {o["Key"]: o["Size"] for o in objects}
    ingestion_responses = []
    m = {}

    def file_ingestion(payload):
        # The same payload the Lambda invoke would carry, through the handler's own parsing.
        response = uf.lambda_handler(json.loads(json.dumps(payload)), None)
        ingestion_responses.append(response)
        return {"Payload": MagicMock(read=lambda: json.dumps(response).encode("utf-8"))}

    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    enforcer.return_value.enforceAPI.return_value = True
    s3 = MagicMock()
    s3.head_object.side_effect = lambda Bucket, Key: {
        "ContentLength": sizes[Key], "VersionId": "v1", "ETag": '"e"'}
    started = iter(range(1, 100))

    with ExitStack() as stack:
        stack.enter_context(patch.object(po, "lookup_existing_asset", return_value={
            "databaseId": DB, "assetId": "asset1.glb", "bucketId": "b1"}))
        stack.enter_context(patch.object(po, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/"}))
        stack.enter_context(patch.object(po, "_fetch_execution_logs", return_value=("", "")))
        # Output step (the cross-call itself is NOT stubbed: process_external_upload is real).
        stack.enter_context(patch.object(po, "verify_get_path_objects",
                                         return_value={"Contents": [dict(o) for o in objects]}))
        stack.enter_context(patch.object(po, "create_external_upload_record", return_value="upl-1"))
        stack.enter_context(patch.object(po, "_stamp_output_objects", return_value=True))
        stack.enter_context(patch.object(po, "_lambda_file_ingestion", side_effect=file_ingestion))
        m["record"] = stack.enter_context(patch.object(po, "record_execution_outputs"))
        stack.enter_context(route_to_engine(po, MagicMock()))
        m["mark"] = stack.enter_context(patch.object(po, "_mark_ingesting", return_value=True))
        m["batch"] = stack.enter_context(patch.object(po.loc, "record_batch"))
        stack.enter_context(patch.object(po.loc, "claim_notification", return_value=None))
        stack.enter_context(patch.object(po, "_finalize_ingestion"))
        stack.enter_context(patch.object(po, "s3c"))
        # File ingestion (identity, authorization and storage stubbed; model and routing real).
        stack.enter_context(patch.object(uf, "request_to_claims",
                                         return_value={"tokens": ["SYSTEM_USER"], "roles": []}))
        stack.enter_context(patch.object(uf, "CasbinEnforcer", enforcer))
        stack.enter_context(patch.object(uf, "get_upload_details", return_value={
            "assetId": "asset1.glb", "databaseId": DB, "uploadType": "assetFile",
            "isExternalUpload": True, "temporaryPrefix": FILES_PREFIX}))
        stack.enter_context(patch.object(uf, "get_asset_details", return_value={
            "assetId": "asset1.glb", "databaseId": DB, "bucketId": "b1",
            "assetLocation": {"Key": "assets/asset1.glb/"}}))
        stack.enter_context(patch.object(uf, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/",
            "bucketRegion": "us-east-1"}))
        stack.enter_context(patch.object(uf, "bucket_region_for_name", return_value="us-east-1"))
        stack.enter_context(patch.object(uf, "get_database_details",
                                         return_value={"databaseId": DB}))
        stack.enter_context(patch.object(uf, "asset_upload_table", MagicMock()))
        stack.enter_context(patch.object(uf, "delete_upload_details", MagicMock()))
        stack.enter_context(patch.object(uf, "validateS3AssetExtensionsAndContentType",
                                         return_value=True))
        stack.enter_context(patch.object(uf, "determine_asset_type", return_value=".glb"))
        stack.enter_context(patch.object(uf, "send_subscription_email", MagicMock()))
        stack.enter_context(patch.object(uf, "log_file_upload", MagicMock()))
        stack.enter_context(patch.object(uf, "s3", s3))
        m["copy"] = stack.enter_context(patch.object(uf, "copy_s3_object", return_value=True))
        stack.enter_context(patch.object(uf, "delete_s3_object"))
        m["queue"] = stack.enter_context(patch.object(uf, "queue_large_file_for_processing",
                                                      return_value=True))
        stack.enter_context(patch.object(uf, "update_asset_attributes"))
        stack.enter_context(patch.object(uf, "copy_jobs_table", return_value=MagicMock()))
        m["start"] = stack.enter_context(patch.object(
            uf.loc, "start_copy_job", side_effect=lambda *a, **k: f"arn:exec:{next(started)}"))
        event = base._event(filesPathKey=FILES_PREFIX, workflowExecutionId=EXEC,
                            workflowDatabaseId=workflow_database_id, outputDatabaseId=DB,
                            awaitOutputIngestion=True)
        response = po.lambda_handler(event, MagicMock())
    return response, ingestion_responses, m


@pytest.mark.unit
@pytest.mark.parametrize("workflow_database_id", WORKFLOW_DATABASES)
@pytest.mark.parametrize("listing", list(LISTINGS))
class TestWriteBackEndToEnd:
    def test_the_ingestion_handler_accepts_the_write_back(self, workflow_database_id, listing):
        _resp, ingestion, _m = _run(workflow_database_id, LISTINGS[listing])
        assert len(ingestion) == 1
        assert ingestion[0]["statusCode"] == 200, ingestion[0].get("body")
        body = json.loads(ingestion[0]["body"])
        assert body["overallSuccess"] is True
        assert {r["relativeKey"] for r in body["fileResults"] if r["success"]} == {
            o["Key"][len(FILES_PREFIX):] for o in LISTINGS[listing]}

    def test_the_run_ends_as_the_outputs_require(self, workflow_database_id, listing):
        resp, _ingestion, m = _run(workflow_database_id, LISTINGS[listing])
        assert resp["statusCode"] == 200
        if listing == "small-only":
            # Nothing to wait on: recorded SUCCEEDED at once.
            assert resp["outputIngestionPending"] is False
            assert m["record"].call_args.kwargs["execution_status"] == "SUCCEEDED"
            m["batch"].assert_not_called()
        else:
            # Waits on its copy jobs; not recorded terminal yet, and not failed.
            assert resp["outputIngestionPending"] is True
            m["record"].assert_not_called()
            m["mark"].assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("workflow_database_id", WORKFLOW_DATABASES)
@pytest.mark.parametrize("listing", ["over-1-gib", "over-5-gib"])
def test_large_outputs_become_copy_jobs_of_the_workflow(workflow_database_id, listing):
    _resp, _ingestion, m = _run(workflow_database_id, LISTINGS[listing])
    large = [o for o in LISTINGS[listing] if o["Size"] > GIB]
    jobs = [c.args[3] for c in m["start"].call_args_list]
    assert len(large) == 1 and len(jobs) == 1
    assert jobs[0]["source"]["key"] == large[0]["Key"]
    # The copy engine finds the run's row by workflowDatabaseId:workflowId, so the GLOBAL keyword
    # must reach the job unchanged.
    assert jobs[0]["workflowDatabaseId"] == workflow_database_id
    assert jobs[0]["batchId"] == EXEC
    m["queue"].assert_not_called()
