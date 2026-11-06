# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An external (workflow output) completion runs once per upload id (#1205).

The output step's Lambda client used botocore's default 60 s read timeout with retries, so a
completion that ran longer was sent again while the first still ran. The repeat found the record
the first call settled and answered 400 "Upload record not found", and the run was recorded FAILED
although its files had landed. Now the first call claims the completion and stores its answer on the
kept record, and a repeated call runs nothing and answers with that result: at once when the first
call finished, after it finishes when it still runs, or 409 when it does not finish in time.

These tests drive the real uploadFile.lambda_handler with the upload records in a moto DynamoDB
table, the record created by the output step's own create_external_upload_record.
"""

import json
import os
import threading
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")
for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402
from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402

po = base.po
EXEC = "0123456789abcdef0123456789abcdef"
TEMP_PREFIX = f"pipelines/p/j/output/{EXEC}/files/"
DATABASE_ID = "db-01"
ASSET_ID = "asset-01"
TABLE = "t-asset-uploads-once"
FILES = ("a.glb", "b.glb")


def _context(remaining_ms=20_000):
    # 20 s left: a repeat waits at most 5 s (REPEATED_COMPLETION_WAIT_MARGIN_SECONDS is 15), so a
    # regression that leaves a record processing fails in seconds instead of holding the suite.
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _payload(upload_id, files=FILES):
    body = {"assetId": ASSET_ID, "databaseId": DATABASE_ID, "uploadType": "assetFile",
            "files": [{"relativeKey": f, "tempKey": TEMP_PREFIX + f} for f in files],
            "workflowId": "wf1", "workflowExecutionId": EXEC, "changeUserId": "SYSTEM_USER",
            "sourceBucket": "run-bucket"}
    return {
        "requestContext": {"http": {"method": "POST",
                                    "path": f"/uploads/{upload_id}/complete/external"}},
        "pathParameters": {"uploadId": upload_id},
        "body": json.dumps(body),
        "lambdaCrossCall": {"userName": "SYSTEM_USER"},
    }


@pytest.fixture
def env():
    with mock_aws(), ExitStack() as stack:
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        enforcer.enforceAPI.return_value = True
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(po, "asset_upload_table", table)
        p(uf, "asset_upload_table", table)
        p(uf, "request_to_claims", return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
        p(uf, "CasbinEnforcer", return_value=enforcer)
        p(uf, "get_asset_details", return_value={
            "assetId": ASSET_ID, "databaseId": DATABASE_ID, "bucketId": "b1",
            "assetLocation": {"Key": f"assets/{ASSET_ID}/"}})
        p(uf, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": "asset-bucket", "baseAssetsPrefix": "assets/",
            "bucketRegion": "us-east-1"})
        p(uf, "bucket_region_for_name", return_value="us-east-1")
        p(uf, "get_database_details", return_value={"databaseId": DATABASE_ID})
        p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(uf, "determine_asset_type", return_value=".glb")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        p(uf, "update_asset_attributes")
        p(uf, "delete_s3_object")
        p(uf, "REPEATED_COMPLETION_POLL_SECONDS", 0.05, create=True)
        copy = p(uf, "copy_s3_object", return_value=True)
        s3 = p(uf, "s3")
        s3.head_object.return_value = {"ContentLength": 7, "VersionId": "v1", "ETag": '"e"'}
        upload_id = po.create_external_upload_record(ASSET_ID, DATABASE_ID, "assetFile", TEMP_PREFIX)
        yield {"table": table, "copy": copy, "upload_id": upload_id}


def _record(env):
    return env["table"].get_item(Key={"uploadId": env["upload_id"], "assetId": ASSET_ID})["Item"]


def _body(response):
    return json.loads(response["body"])


@pytest.mark.unit
class TestARepeatedCompletionAnswersTheFirstResult:
    def test_a_repeat_after_the_first_finished_gets_its_result_and_runs_nothing(self, env):
        first = uf.lambda_handler(_payload(env["upload_id"]), _context())
        second = uf.lambda_handler(_payload(env["upload_id"]), _context())
        assert first["statusCode"] == 200
        # Was 400 "Upload record not found": the first call had deleted the record.
        assert second["statusCode"] == 200
        assert _body(second) == _body(first)
        assert _body(second)["overallSuccess"] is True
        # Each file was copied once, by the first call only.
        assert env["copy"].call_count == len(FILES)

    def test_the_record_is_kept_with_its_status_and_result(self, env):
        uf.lambda_handler(_payload(env["upload_id"]), _context())
        record = _record(env)
        assert record["status"] == "completed"
        assert int(record["completionStatusCode"]) == 200
        assert "completionResult" in record and int(record["expiresAt"]) > time.time()

    def test_a_repeat_while_the_first_still_runs_waits_for_its_result(self, env):
        started, release = threading.Event(), threading.Event()
        real_copy = env["copy"].side_effect

        def slow_copy(*args, **kwargs):
            started.set()
            release.wait(5)
            return True

        env["copy"].side_effect = slow_copy
        results = {}
        first = threading.Thread(target=lambda: results.setdefault(
            "first", uf.lambda_handler(_payload(env["upload_id"]), _context())))
        first.start()
        assert started.wait(5)
        threading.Timer(0.3, release.set).start()
        second = uf.lambda_handler(_payload(env["upload_id"]), _context())
        first.join(10)
        env["copy"].side_effect = real_copy
        assert second["statusCode"] == 200
        assert _body(second) == _body(results["first"])
        assert env["copy"].call_count == len(FILES)

    def test_a_repeat_the_first_does_not_answer_in_time_gets_409(self, env):
        env["table"].update_item(
            Key={"uploadId": env["upload_id"], "assetId": ASSET_ID},
            UpdateExpression="SET #s = :p", ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":p": "processing"})
        response = uf.lambda_handler(_payload(env["upload_id"]), _context(remaining_ms=15_100))
        assert response["statusCode"] == 409
        assert _body(response)["message"] == uf.REPEATED_COMPLETION_IN_PROGRESS
        env["copy"].assert_not_called()

    def test_a_first_call_that_failed_is_answered_the_same_way(self, env):
        with patch.object(uf, "get_database_details", return_value=None):
            first = uf.lambda_handler(_payload(env["upload_id"]), _context())
        second = uf.lambda_handler(_payload(env["upload_id"]), _context())
        assert first["statusCode"] == 400
        assert (second["statusCode"], _body(second)) == (400, _body(first))
        assert _record(env)["status"] == "failed"
        env["copy"].assert_not_called()

    def test_an_all_failed_first_call_is_answered_409_with_its_file_results(self, env):
        env["copy"].return_value = False
        first = uf.lambda_handler(_payload(env["upload_id"]), _context())
        env["copy"].return_value = True
        second = uf.lambda_handler(_payload(env["upload_id"]), _context())
        assert first["statusCode"] == 409
        assert (second["statusCode"], _body(second)) == (409, _body(first))
        assert env["copy"].call_count == len(FILES)

    def test_a_record_completed_without_a_stored_result_gets_409(self, env):
        # A record settled by code that stored no result (an upgrade with a call in flight).
        env["table"].update_item(
            Key={"uploadId": env["upload_id"], "assetId": ASSET_ID},
            UpdateExpression="SET #s = :c", ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": "completed_with_errors"})
        response = uf.lambda_handler(_payload(env["upload_id"]), _context())
        assert response["statusCode"] == 409
        assert _body(response)["message"] == uf.REPEATED_COMPLETION_RESULT_UNAVAILABLE

    def test_an_unknown_upload_id_is_still_refused(self, env):
        response = uf.lambda_handler(_payload("y-unknown"), _context())
        assert response["statusCode"] == 400
        assert _body(response)["message"].endswith("Upload record not found")

    def test_a_result_too_large_to_store_is_answered_409(self, env):
        with patch.object(uf, "MAX_STORED_COMPLETION_RESULT_BYTES", 10):
            first = uf.lambda_handler(_payload(env["upload_id"]), _context())
        second = uf.lambda_handler(_payload(env["upload_id"]), _context())
        assert first["statusCode"] == 200
        assert second["statusCode"] == 409
        assert _body(second)["message"] == uf.REPEATED_COMPLETION_RESULT_UNAVAILABLE
        assert env["copy"].call_count == len(FILES)
