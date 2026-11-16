# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An output block of more than 1,000 files is written back (#1204).

The external completion accepts at most MAX_FILES_PER_UPLOAD_REQUEST (1,000) files per call. The
output step sent every file in one call, so a run with 1,001 outputs was refused with a 400 and
wrote nothing back. These tests run the output step against the REAL uploadFile handler (the
cross-call is answered by uploadFile.lambda_handler in-process, with the upload records in a moto
DynamoDB table), for GLOBAL and database workflows:

- 1,001 and 2,500 files land, in calls of at most 1,000 files, each with its own upload record;
- large files of every call join the run's one ingestion batch, and the batch progress counts them;
- a call that fails names its files in the run's error, while the other calls' files land;
- a '.previewFile.' travels in the same call as its base file.
"""

import io
import json
import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
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

from backend.tests.copyEngineRouting import route_to_engine  # noqa: E402
from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402
from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402

po = base.po
loc = po.loc
GIB = 1024 ** 3
EXEC = "0123456789abcdef0123456789abcdef"
FILES_PREFIX = f"pipelines/p1/JOB/output/{EXEC}/files/"
DATABASE_ID = "db-01"
ASSET_ID = "asset-01"
UPLOADS_TABLE = "t-asset-uploads-many"


def _listing(count, large=None):
    """count staged outputs in listing order; `large` maps a position to the name of a 3 GiB file
    placed there, every other file is small."""
    large = large or {}
    contents = []
    for i in range(count):
        name = large.get(i, f"part/f{i:05d}.glb")
        size = 3 * GIB if i in large else 7
        contents.append({"Key": FILES_PREFIX + name, "Size": size, "ContentType": "model/gltf-binary",
                         "VersionId": "v1", "Metadata": {}})
    return {"Contents": contents}


def _event(workflow_database_id, **overrides):
    body = {
        "outputAssetId": ASSET_ID,
        "outputDatabaseId": DATABASE_ID,
        "workflowExecutionId": EXEC,
        "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": workflow_database_id,
        "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": base.RUN_BUCKET,
        "executingUserName": "SYSTEM_USER",
        "executingRequestContext": {"http": {}},
        "filesPathKey": FILES_PREFIX,
    }
    body.update(overrides)
    return {"body": body}


class _Harness:
    """The output step wired to the real uploadFile handler."""

    def __init__(self, listing, fail_copy=(), fail_call=None):
        self.listing = listing
        self.sizes = {o["Key"]: o["Size"] for o in listing["Contents"]}
        self.fail_copy = set(fail_copy)
        self.fail_call = fail_call
        self.calls = []

    def invoke_upload(self, payload):
        """Stand-in for the Lambda invoke: uploadFile.lambda_handler answers in-process."""
        body = json.loads(payload["body"])
        self.calls.append(body)
        if self.fail_call is not None and len(self.calls) == self.fail_call:
            raise RuntimeError("cross-call failed")
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 900_000
        response = uf.lambda_handler(json.loads(json.dumps(payload)), context)
        return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}

    def head(self, Bucket, Key):
        return {"ContentLength": self.sizes[Key], "VersionId": "v1", "ETag": '"e"'}

    def copy(self, source_bucket, source_key, *args, **kwargs):
        return source_key not in self.fail_copy

    def run(self, event):
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=UPLOADS_TABLE,
            KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        enforcer.enforceAPI.return_value = True
        m = {}
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
            # Output step side.
            p(po, "lookup_existing_asset", return_value={
                "databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "b1"})
            p(po, "get_default_bucket_details", return_value={
                "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/"})
            p(po, "_fetch_execution_logs", return_value=("", ""))
            p(po, "verify_get_path_objects", return_value=self.listing)
            p(po, "asset_upload_table", table)
            m["stamp"] = p(po, "_stamp_output_objects", return_value=True)
            p(po, "_lambda_file_ingestion", side_effect=self.invoke_upload)
            m["record"] = p(po, "record_execution_outputs")
            stack.enter_context(route_to_engine(po, MagicMock()))
            m["mark"] = p(po, "_mark_ingesting", return_value=True)
            m["batch"] = p(po.loc, "record_batch")
            p(po.loc, "claim_notification", return_value=None)
            p(po.loc, "recorded_batch_jobs", return_value={})
            m["s3c"] = p(po, "s3c")
            # uploadFile side: the handler, its model and its upload records are real.
            p(uf, "request_to_claims", return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
            p(uf, "CasbinEnforcer", return_value=enforcer)
            p(uf, "asset_upload_table", table)
            p(uf, "get_asset_details", return_value={
                "assetId": ASSET_ID, "databaseId": DATABASE_ID, "bucketId": "b1",
                "assetLocation": {"Key": f"assets/{ASSET_ID}/"}})
            p(uf, "get_default_bucket_details", return_value={
                "bucketId": "b1", "bucketName": base.ASSET_BUCKET, "baseAssetsPrefix": "assets/",
                "bucketRegion": "us-east-1"})
            p(uf, "bucket_region_for_name", return_value="us-east-1")
            p(uf, "get_database_details", return_value={"databaseId": DATABASE_ID})
            p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
            p(uf, "determine_asset_type", return_value=".glb")
            p(uf, "send_subscription_email")
            p(uf, "log_file_upload")
            p(uf, "update_asset_attributes")
            p(uf, "delete_s3_object")
            m["copy"] = p(uf, "copy_s3_object", side_effect=self.copy)
            m["queue"] = p(uf, "queue_large_file_for_processing", return_value=True)
            p(uf, "copy_jobs_table", return_value=MagicMock())
            m["start"] = p(uf.loc, "start_copy_job")
            s3 = p(uf, "s3")
            s3.head_object.side_effect = self.head
            response = po.lambda_handler(event, MagicMock())
            m["records"] = table.scan()["Items"]
        return response, m


def _files_sent(harness):
    return [f["tempKey"] for call in harness.calls for f in call["files"]]


@pytest.mark.unit
@pytest.mark.parametrize("workflow_database_id", ["GLOBAL", "wfdb-1"])
class TestManyOutputsLand:
    @pytest.mark.parametrize("count", [1001, 2500])
    def test_every_file_lands_in_calls_within_the_cap(self, workflow_database_id, count):
        harness = _Harness(_listing(count))
        with mock_aws():
            response, m = harness.run(_event(workflow_database_id))
        assert response["statusCode"] == 200
        sizes = [len(call["files"]) for call in harness.calls]
        assert max(sizes) <= po.MAX_FILES_PER_COMPLETION_CALL
        assert len(sizes) == -(-count // po.MAX_FILES_PER_COMPLETION_CALL)
        # Every file is sent exactly once, and every one landed.
        assert sorted(_files_sent(harness)) == sorted(o["Key"] for o in harness.listing["Contents"])
        assert m["copy"].call_count == count
        kwargs = m["record"].call_args.kwargs
        assert kwargs["execution_status"] == "SUCCEEDED", kwargs["execution_error"]
        assert len([f for f in kwargs["output_files"] if f["fileType"] == "file"]) == count
        # One upload record per call; each call's files were stamped with that call's record.
        stamps = m["stamp"].call_args_list
        assert len(stamps) == len(harness.calls) == len(sizes) >= 2
        assert len({call_args.args[3] for call_args in stamps}) == len(sizes)
        assert sorted(call["files"][0]["tempKey"] for call in harness.calls) == sorted(
            call_args.args[0][0]["Key"] for call_args in stamps)
        # Each call's record is kept, settled, with its result for a repeated call (#1205).
        assert len(m["records"]) == len(sizes)
        assert {r["status"] for r in m["records"]} == {"completed"}
        assert {int(r["completionStatusCode"]) for r in m["records"]} == {200}

    def test_large_files_of_every_call_join_the_one_batch(self, workflow_database_id):
        # One large file in each of the three calls (positions 0, 1500 and 2400 of 2,500).
        large = {0: "big/a.e57", 1500: "big/b.e57", 2400: "big/c.e57"}
        harness = _Harness(_listing(2500, large=large))
        with mock_aws():
            response, m = harness.run(_event(workflow_database_id, awaitOutputIngestion=True))
        assert response["outputIngestionPending"] is True
        m["queue"].assert_not_called()
        assert len(harness.calls) == 3
        assert [sum(f["tempKey"].endswith(".e57") for f in call["files"]) for call in harness.calls] == [1, 1, 1]
        jobs = [c.args[3] for c in m["start"].call_args_list]
        assert len(jobs) == len(large)
        assert sorted(j["relativeKey"] for j in jobs) == sorted(large.values())
        assert {j["batchId"] for j in jobs} == {EXEC}
        assert {j["workflowDatabaseId"] for j in jobs} == {workflow_database_id}
        # The batch records every job, wherever its call fell, and progress counts their bytes.
        batch_id, job_ids = m["batch"].call_args.args[1:3]
        assert batch_id == EXEC
        assert sorted(job_ids) == sorted(loc.job_id_for(EXEC, rel) for rel in large.values())
        assert m["mark"].call_args.args[1:3] == (3, 9 * GIB)
        m["record"].assert_not_called()
        state = json.loads(m["s3c"].put_object.call_args.kwargs["Body"])
        assert len([d for d in state["collectedOutputFiles"] if d["fileType"] == "file"]) == 2500


@pytest.mark.unit
class TestPartialFailureNamesItsFiles:
    def test_a_file_that_did_not_land_is_named_and_the_others_land(self):
        bad = FILES_PREFIX + "part/f01500.glb"
        harness = _Harness(_listing(2500), fail_copy=[bad])
        with mock_aws():
            _response, m = harness.run(_event("GLOBAL"))
        kwargs = m["record"].call_args.kwargs
        assert kwargs["execution_status"] == "FAILED"
        assert kwargs["execution_error"] == (
            "The asset file write-back failed. "
            "Writing output file 'part/f01500.glb' into the asset failed.")
        rows = {f["s3Key"] for f in kwargs["output_files"] if f["fileType"] == "file"}
        assert len(rows) == 2499 and bad not in rows

    def test_a_failed_call_names_its_files_and_the_other_calls_land(self):
        harness = _Harness(_listing(2500), fail_call=2)
        with mock_aws():
            _response, m = harness.run(_event("GLOBAL"))
        kwargs = m["record"].call_args.kwargs
        assert kwargs["execution_status"] == "FAILED"
        error = kwargs["execution_error"]
        assert error.startswith("The asset file write-back failed. Writing output file 'part/f01000.glb'")
        # The first 20 names, then a count of the rest of the failed call's 1,000 files.
        assert error.count("Writing output file") == po.MAX_NAMED_WRITE_BACK_FAILURES
        assert error.endswith("980 more output files were not written into the asset.")
        rows = [f for f in kwargs["output_files"] if f["fileType"] == "file"]
        assert len(rows) == 1500


@pytest.mark.unit
class TestCompletionChunks:
    def _objects(self, names):
        return [{"Key": FILES_PREFIX + n} for n in names]

    def test_a_preview_file_travels_with_its_base_file(self):
        names = [f"f{i:04d}.glb" for i in range(999)] + ["z.glb", "z.glb.previewFile.png"]
        chunks = po._completion_chunks(self._objects(names), max_files=1000)
        assert [len(c) for c in chunks] == [999, 2]
        assert [o["Key"][len(FILES_PREFIX):] for o in chunks[1]] == ["z.glb", "z.glb.previewFile.png"]

    def test_listing_order_is_kept_and_no_file_is_lost(self):
        names = [f"f{i:04d}.glb" for i in range(2500)]
        chunks = po._completion_chunks(self._objects(names), max_files=1000)
        assert [len(c) for c in chunks] == [1000, 1000, 500]
        assert [o["Key"] for c in chunks for o in c] == [FILES_PREFIX + n for n in names]

    def test_a_group_larger_than_a_call_is_split(self):
        names = ["a.glb"] + [f"a.glb.previewFile.{i}.png" for i in range(4)]
        chunks = po._completion_chunks(self._objects(names), max_files=2)
        assert [len(c) for c in chunks] == [2, 2, 1]
