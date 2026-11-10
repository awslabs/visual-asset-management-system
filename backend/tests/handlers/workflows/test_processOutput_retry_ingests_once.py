# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A retried process-outputs state writes each output into the asset once (#1123).

The output step state is retried on LargeObjectCopyEngineBusy (a throttled copy engine) and, before
this change already, on States.Timeout and the Lambda faults. A retry runs the whole ingest step
again with outputStepAttempt > 0. These tests run two attempts of the real output step against the
real uploadFile handler, with the run bucket, the asset bucket, the upload records and the copy jobs
table in moto, and count what landed:

- the first attempt moves every small output into the asset (copy, then delete of the staged
  source) and hands the large one to a copy job, then fails at the copy engine (busy) or times out;
- the second attempt lists the staged prefix again: the small outputs are no longer there, so they
  are not written again (one asset object version each), and the large one maps to the SAME copy
  job (its id derives from the run and the output key; starting it again is a no-op);
Why no duplicate: the small-file write-back is a move (copy, then delete of the staged source), so
a retry's listing no longer holds a file an earlier attempt wrote; and a large output's copy job id
is job_id_for(workflowExecutionId, output key), so the retry's start of it records nothing new and
its StartExecution answers ExecutionAlreadyExists.

Known gap, pinned as a strict xfail below: the retried attempt RECORDS only the outputs it still sees
staged plus the earlier attempts' copy jobs, so a small output the first attempt moved lands in the
asset but is missing from the run's output rows. The same holds for the States.Timeout retry of the
process-outputs state on development (no attempt merge there at all). Tracked separately.

The "large" threshold is lowered to 100 bytes so moto can hold the files.
"""

import io
import json
import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
    "SEND_EMAIL_FUNCTION_NAME": "test-send-email-function",
    "PRESIGNED_URL_TIMEOUT_SECONDS": "3600",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402
from backend.tests.copyEngineRouting import engine, route_to_engine  # noqa: E402
from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402

po = base.po
loc = po.loc
REGION = "us-east-1"
EXEC = "0123456789abcdef0123456789abcdef"
BASE_PREFIX = "pipelines/p1/JOB/output/"
FILES_PREFIX = f"{BASE_PREFIX}{EXEC}/files/"
DATABASE_ID = "db-01"
ASSET_ID = "asset-01"
SMALL = ("a.glb", "b.glb")
LARGE = "big.e57"
THRESHOLD = 100


def _event(attempt):
    return {"body": {
        "outputAssetId": ASSET_ID, "outputDatabaseId": DATABASE_ID, "workflowExecutionId": EXEC,
        "endStatePipelineExecutionId": "P1", "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": base.RUN_BUCKET,
        "executingUserName": "SYSTEM_USER", "executingRequestContext": {"http": {}},
        "filesPathKey": FILES_PREFIX, "awaitOutputIngestion": True, "outputStepAttempt": attempt}}


class _Busy:
    """The output step's engine calls, failing the first recordBatch as a throttled engine."""

    def __init__(self, real, fail_on):
        self.real, self.fail_on, self.failed = real, fail_on, False

    def __call__(self, operation, **fields):
        if operation == self.fail_on and not self.failed:
            self.failed = True
            raise loc.LargeObjectCopyEngineBusy("throttled")
        return self.real(operation, **fields)


def _tables():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    uploads = ddb.create_table(
        TableName="t-asset-uploads-retry",
        KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                   {"AttributeName": "assetId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                              {"AttributeName": "assetId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    jobs = ddb.create_table(
        TableName=engine.jobs_table_name,
        KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"},
                              {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{"IndexName": loc.WORKFLOW_EXECUTION_ID_INDEX,
                                 "KeySchema": [{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
                                 "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")
    return uploads, jobs


def _buckets(s3):
    for bucket in (base.RUN_BUCKET, base.ASSET_BUCKET):
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    for name in SMALL:
        s3.put_object(Bucket=base.RUN_BUCKET, Key=FILES_PREFIX + name, Body=b"small",
                      ContentType="model/gltf-binary")
    s3.put_object(Bucket=base.RUN_BUCKET, Key=FILES_PREFIX + LARGE, Body=b"x" * (THRESHOLD * 2),
                  ContentType="application/octet-stream")


def _listing(s3):
    contents = s3.list_objects_v2(Bucket=base.RUN_BUCKET, Prefix=FILES_PREFIX).get("Contents", [])
    return {"Contents": [{**o, "ContentType": "model/gltf-binary"} for o in contents]} if contents else {}


def _start_execution(**kwargs):
    if kwargs["name"] in _start_execution.started:
        raise ClientError({"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")
    _start_execution.started.append(kwargs["name"])
    return {"executionArn": "arn:aws:states:" + REGION + ":1:execution:LargeObjectCopy-x:" + kwargs["name"]}


def _run_attempts(fail):
    """Run attempt 0 (failing as `fail` says) and attempt 1; return what landed."""
    _start_execution.started = []
    s3 = boto3.client("s3", region_name=REGION)
    _buckets(s3)
    uploads, jobs = _tables()
    sfn = MagicMock(**{"start_execution.side_effect": _start_execution})
    enforcer = MagicMock(**{"enforce.return_value": True, "enforceAPI.return_value": True})

    def invoke_upload(payload):
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": 900_000})
        response = uf.lambda_handler(json.loads(json.dumps(payload)), context)
        return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}

    results = []
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(loc, "LARGE_FILE_THRESHOLD_BYTES", THRESHOLD)
        p(uf, "LARGE_FILE_THRESHOLD_BYTES", THRESHOLD)
        p(po, "lookup_existing_asset", return_value={
            "databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "b1"})
        p(po, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/"})
        p(po, "_fetch_execution_logs", return_value=("", ""))
        p(po, "verify_get_path_objects", side_effect=lambda bucket, prefix: _listing(s3))
        p(po, "asset_upload_table", uploads)
        p(po, "_stamp_output_objects", return_value=True)
        p(po, "_lambda_file_ingestion", side_effect=invoke_upload)
        record = p(po, "record_execution_outputs")
        p(po, "_mark_ingesting", return_value=True)
        p(po, "s3c", s3)
        stack.enter_context(route_to_engine(po, jobs, sfn_client=MagicMock()))
        p(engine, "dynamodb_client", boto3.client("dynamodb", region_name=REGION))
        p(uf, "request_to_claims", return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
        p(uf, "CasbinEnforcer", return_value=enforcer)
        p(uf, "asset_upload_table", uploads)
        p(uf, "get_asset_details", return_value={
            "assetId": ASSET_ID, "databaseId": DATABASE_ID, "bucketId": "b1",
            "assetLocation": {"Key": f"assets/{ASSET_ID}/"}})
        p(uf, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/",
            "bucketRegion": REGION})
        p(uf, "bucket_region_for_name", return_value=REGION)
        p(uf, "get_database_details", return_value={"databaseId": DATABASE_ID})
        p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(uf, "determine_asset_type", return_value=".glb")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        p(uf, "update_asset_attributes")
        p(uf, "s3", s3)
        p(uf.s3_resource.meta, "client", s3)
        p(uf, "copy_jobs_table", return_value=jobs)
        p(uf, "sfn_client", sfn)
        p(uf.loc, "state_machine_arn", return_value="arn:aws:states:us-east-1:1:stateMachine:LargeObjectCopy-x")

        real_engine = po._engine
        if fail == "busy":
            p(po, "_engine", _Busy(real_engine, loc.OUTPUT_STEP_RECORD_BATCH))
            with pytest.raises(loc.LargeObjectCopyEngineBusy):
                po.lambda_handler(_event(0), MagicMock())
        else:
            # A timeout: the attempt ends after its ingestion cross-calls, before it records the batch.
            calls = []

            def begin(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise TimeoutError("Task timed out")
                return _REAL_BEGIN(*args, **kwargs)

            p(po, "_begin_waiting_for_ingestion", side_effect=begin)
            with pytest.raises(TimeoutError):
                po.lambda_handler(_event(0), MagicMock())
        results.append(po.lambda_handler(_event(1), MagicMock()))
        versions = {}
        for v in s3.list_object_versions(Bucket=base.ASSET_BUCKET).get("Versions", []):
            versions.setdefault(v["Key"], []).append(v["VersionId"])
        state = {}
        state_objects = s3.list_objects_v2(Bucket=base.RUN_BUCKET).get("Contents", [])
        for o in state_objects:
            if o["Key"].endswith(po.INGESTION_STATE_FILENAME):
                state = json.loads(s3.get_object(Bucket=base.RUN_BUCKET, Key=o["Key"])["Body"].read())
        return {"answer": results[-1], "versions": versions, "jobs": jobs.scan()["Items"],
                "started": list(_start_execution.started), "state": state, "record": record}


_REAL_BEGIN = po._begin_waiting_for_ingestion


def _asset_key(name):
    return f"assets/{ASSET_ID}/{name}"


@pytest.mark.unit
@pytest.mark.aws
@pytest.mark.parametrize("fail", ["busy", "timeout"])
class TestARetriedIngestWritesEachOutputOnce:
    def test_each_small_output_has_one_asset_version(self, fail):
        with mock_aws():
            landed = _run_attempts(fail)
        for name in SMALL:
            assert len(landed["versions"].get(_asset_key(name), [])) == 1, landed["versions"]

    def test_the_large_output_has_one_copy_job_and_one_execution(self, fail):
        with mock_aws():
            landed = _run_attempts(fail)
        job_rows = [j for j in landed["jobs"] if not str(j["jobId"]).startswith(loc.BATCH_KEY_PREFIX)]
        assert [j["relativeKey"] for j in job_rows] == [LARGE]
        assert job_rows[0]["jobId"] == loc.job_id_for(EXEC, LARGE)
        assert len(landed["started"]) == 1
        assert landed["answer"]["outputIngestionPending"] is True

    @pytest.mark.xfail(strict=True, reason="a retried ingest records only what it still sees staged; "
                       "tracked as a separate issue (pre-existing on development's timeout retry)")
    def test_the_outputs_the_first_attempt_moved_are_still_recorded(self, fail):
        with mock_aws():
            landed = _run_attempts(fail)
        described = sorted(d["relativeFilePath"].lstrip("/") for d in landed["state"].get("collectedOutputFiles", [])
                           if d.get("fileType") == "file")
        assert described == sorted([*SMALL, LARGE])
