# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two asynchronous copies of one workflow output cannot both write it (#1271).

A retried workflow end-state step can queue a large output twice. Before it copies, the large-file
worker takes a conditional claim on the output's row in the run's ingest ledger (the output-files
table, partition `ingest#<workflowExecutionId>`, sort key `copy:<destination key>`). These tests run
the real worker against moto S3 and DynamoDB:

- two copies of the same output at the same moment: only the claim holder writes; the other returns
  its message to the queue and, redelivered, finishes without writing (one asset version);
- a claim left by a crashed holder is taken over once older than COPY_CLAIM_TAKEOVER_SECONDS, and not
  before; the holder's own redelivered message takes it back at once;
- a claim on one output never blocks another output of the same run;
- a failed copy releases the claim; an unreadable claim table returns the message without deleting
  the staged output; a plain (non-workflow) upload takes no claim.
"""

import json
import threading
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.assets import sqsUploadFileLarge as large
from backend.backend.common.workflows import executionOutputs as eo

REGION = "us-east-1"
RUN_BUCKET = "run-bucket-1271-claim"
ASSET_BUCKET = "asset-bucket-1271-claim"
EXEC = "0123456789abcdef0123456789abcdef"
STAGED = f"pipelines/p1/JOB/output/{EXEC}/files/"
# getattr: the file also runs, and fails test by test, against code that predates the claim.
TABLE = getattr(large, "pipeline_execution_output_files_table", "t-of")


def _file_info(name="big.e57", execution_id=EXEC):
    return {
        "bucketName": ASSET_BUCKET, "sourceBucketName": RUN_BUCKET,
        "tempS3Key": STAGED + name, "finalS3Key": f"assets/asset-01/{name}",
        "relativeKey": name, "uploadType": "assetFile", "databaseId": "db-01",
        "assetId": "asset-01", "uploadId": "y1", "uploadIdS3": "external", "parts": [],
        "changeUserId": "SYSTEM_USER", "workflowId": "wf1", "workflowExecutionId": execution_id,
    }


def _ids(message_id):
    return {"messageId": message_id, "recordCorrelationId": f"r-{message_id}"}


class _Env:
    """moto buckets + the output-files table, with the worker's clients pointed at them."""

    def __init__(self, stack, names=("big.e57",)):
        self.s3 = boto3.client("s3", region_name=REGION)
        for bucket in (RUN_BUCKET, ASSET_BUCKET):
            self.s3.create_bucket(Bucket=bucket)
            self.s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for name in names:
            self.s3.put_object(Bucket=RUN_BUCKET, Key=STAGED + name, Body=b"x" * 64)
        self.ddb = boto3.resource("dynamodb", region_name=REGION)
        self.table = self.ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                       {"AttributeName": "fileType:relativeFilePath", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                                  {"AttributeName": "fileType:relativeFilePath", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(large, "s3", self.s3)
        p(large.s3_resource.meta, "client", self.s3)
        p(large, "dynamodb", self.ddb, create=True)
        p(large, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(large, "update_asset_after_file_processing")

    def versions(self, name="big.e57"):
        return [v for v in self.s3.list_object_versions(Bucket=ASSET_BUCKET).get("Versions", [])
                if v["Key"] == f"assets/asset-01/{name}"]

    def claim(self, name="big.e57"):
        return self.table.get_item(Key={
            "pipelineExecutionId": "ingest#" + EXEC,
            "fileType:relativeFilePath": "copy:assets/asset-01/" + name}).get("Item")

    def put_claim(self, holder, age_seconds, name="big.e57", state="copying"):
        self.table.put_item(Item={
            "pipelineExecutionId": "ingest#" + EXEC,
            "fileType:relativeFilePath": "copy:assets/asset-01/" + name,
            "claimHolder": holder, "claimedAt": int(time.time()) - age_seconds, "claimState": state})


def _held():
    """The exception a worker raises when another holds the claim (absent on code without claims)."""
    return getattr(large, "CopyClaimHeld", Exception)


def _takeover_seconds():
    return getattr(eo, "COPY_CLAIM_TAKEOVER_SECONDS", 20 * 60)


@pytest.mark.unit
@pytest.mark.aws
class TestTwoConcurrentCopiesWriteOnce:
    def test_only_the_claim_holder_writes_and_the_other_reports_its_outcome(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            real_copy = large.copy_s3_object
            in_copy, release = threading.Event(), threading.Event()

            def slow_copy(*args, **kwargs):
                if threading.current_thread().name == "holder":
                    in_copy.set()
                    release.wait(10)
                return real_copy(*args, **kwargs)

            stack.enter_context(patch.object(large, "copy_s3_object", side_effect=slow_copy))
            results = {}
            holder = threading.Thread(
                name="holder",
                target=lambda: results.__setitem__(
                    "holder", large.validate_and_move_large_file(_file_info(), _ids("m1"))))
            holder.start()
            assert in_copy.wait(10)
            # The second message arrives while the holder is copying.
            try:
                results["second"] = large.validate_and_move_large_file(_file_info(), _ids("m2"))
            except _held():
                results["second"] = "requeued"
            release.set()
            holder.join(10)
            assert results == {"holder": True, "second": "requeued"}
            # Redelivered after the visibility timeout: the holder copied, so it only reports success.
            assert large.validate_and_move_large_file(_file_info(), _ids("m2")) is True
            assert len(env.versions()) == 1
            assert "Contents" not in env.s3.list_objects_v2(Bucket=RUN_BUCKET)


@pytest.mark.unit
@pytest.mark.aws
class TestACrashedHoldersClaim:
    def test_is_taken_over_after_the_takeover_age(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            env.put_claim("m1", _takeover_seconds() + 30)
            assert large.validate_and_move_large_file(_file_info(), _ids("m2")) is True
            assert len(env.versions()) == 1
            assert env.claim()["claimHolder"] == "m2"
            assert env.claim()["claimState"] == "copied"

    def test_is_not_taken_over_before_the_takeover_age(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            env.put_claim("m1", _takeover_seconds() - 60)
            with pytest.raises(_held()):
                large.validate_and_move_large_file(_file_info(), _ids("m2"))
            assert env.versions() == []
            # The staged output is the holder's: it is still there.
            assert env.s3.list_objects_v2(Bucket=RUN_BUCKET)["KeyCount"] == 1

    def test_the_holders_own_redelivered_message_takes_it_back_at_once(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            env.put_claim("m1", 30)
            assert large.validate_and_move_large_file(_file_info(), _ids("m1")) is True
            assert len(env.versions()) == 1


@pytest.mark.unit
@pytest.mark.aws
class TestAClaimNeverBlocksAnotherOutput:
    def test_a_held_claim_on_one_output_leaves_the_others_free(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack, names=("big.e57", "other.e57"))
            env.put_claim("m1", 30, name="big.e57")
            assert large.validate_and_move_large_file(_file_info("other.e57"), _ids("m2")) is True
            assert len(env.versions("other.e57")) == 1
            assert env.claim("other.e57")["claimState"] == "copied"
            assert env.claim("big.e57")["claimHolder"] == "m1"

    def test_the_same_output_of_another_run_is_not_blocked(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            env.put_claim("m1", 30)
            other_run = "f" * 32
            assert large.validate_and_move_large_file(
                _file_info(execution_id=other_run), _ids("m2")) is True
            assert len(env.versions()) == 1


@pytest.mark.unit
@pytest.mark.aws
class TestClaimEdges:
    def test_a_failed_copy_releases_the_claim(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            stack.enter_context(patch.object(large, "copy_s3_object", return_value=False))
            assert large.validate_and_move_large_file(_file_info(), _ids("m1")) is False
            assert "claimHolder" not in env.claim()
            assert env.s3.list_objects_v2(Bucket=RUN_BUCKET)["KeyCount"] == 1

    def test_an_unreadable_claim_table_returns_the_message_and_keeps_the_staged_output(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            broken = MagicMock(**{"Table.return_value.update_item.side_effect": RuntimeError("ddb down")})
            stack.enter_context(patch.object(large, "dynamodb", broken))
            with pytest.raises(large.CopyClaimHeld):
                large.process_large_file(_file_info(), _ids("m1"))
            assert env.versions() == []
            assert env.s3.list_objects_v2(Bucket=RUN_BUCKET)["KeyCount"] == 1

    def test_a_plain_upload_takes_no_claim(self):
        with mock_aws(), ExitStack() as stack:
            env = _Env(stack)
            info = _file_info(execution_id=None)
            info["workflowId"] = None
            assert large.validate_and_move_large_file(info, _ids("m1")) is True
            assert len(env.versions()) == 1
            assert env.table.scan()["Items"] == []

    def test_the_handler_returns_a_held_message_to_the_queue(self):
        event = {"Records": [{"messageId": "m2", "body": json.dumps({"fileInfo": _file_info()})}]}
        with patch.object(large, "validate_sqs_message", return_value=True), \
                patch.object(large, "process_large_file", side_effect=large.CopyClaimHeld("held")):
            with pytest.raises(large.CopyClaimHeld):
                large.lambda_handler(event, MagicMock(aws_request_id="req"))
