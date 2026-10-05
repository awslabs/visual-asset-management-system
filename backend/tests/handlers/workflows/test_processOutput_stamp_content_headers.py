# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""processWorkflowExecutionOutput provenance stamp keeps a staged output's content headers.

The stamp rewrites each staged output object in place with MetadataDirective=REPLACE to add the
asset/upload ids. A REPLACE copy resets every system-defined header the request does not restate,
so the copy restates the headers the listing HEAD read: a pipeline that writes a gzip-encoded or
cache-controlled output keeps those headers through ingestion.
"""

import datetime
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

BUCKET = "run-io-bucket"
KEY = "pipelines/p1/JOB/output/E1/files/scene.json"
EXPIRES = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
FULL_HEAD = {
    "ContentType": "application/json",
    "ContentEncoding": "gzip",
    "CacheControl": "max-age=60",
    "ContentDisposition": 'attachment; filename="scene.json"',
    "ContentLanguage": "en",
    "Expires": EXPIRES,
    "VersionId": "v1",
    "Metadata": {"vams-changesource": "workflowExecution"},
}


@pytest.fixture
def s3(monkeypatch):
    s3c = MagicMock(name="s3c")
    s3r = MagicMock(name="s3r")
    monkeypatch.setattr(po, "s3c", s3c)
    monkeypatch.setattr(po, "s3r", s3r)
    return s3c, s3r


def _copy_extra_args(s3r):
    s3r.Object.return_value.copy.assert_called_once()
    return s3r.Object.return_value.copy.call_args.kwargs["ExtraArgs"]


class TestStampKeepsContentHeaders:
    def test_listed_object_stamp_restates_every_content_header(self, s3):
        s3c, s3r = s3
        s3c.head_object.return_value = dict(FULL_HEAD)
        objects = [{"Key": KEY}]

        po._head_listed_objects(BUCKET, objects)
        assert po._stamp_output_objects(objects, "asset1", "db1", "up1", BUCKET) is True

        # The stamp reused the listing HEAD rather than reading the object again.
        assert s3c.head_object.call_count == 1
        args = _copy_extra_args(s3r)
        assert args["MetadataDirective"] == "REPLACE"
        assert args["ContentType"] == "application/json"
        assert args["ContentEncoding"] == "gzip"
        assert args["CacheControl"] == "max-age=60"
        assert args["ContentDisposition"] == 'attachment; filename="scene.json"'
        assert args["ContentLanguage"] == "en"
        assert args["Expires"] == EXPIRES
        assert args["ACL"] == "bucket-owner-full-control"
        assert args["Metadata"]["vams-changesource"] == "workflowExecution"
        assert args["Metadata"][po.ASSET_ID_METADATA_KEY] == "asset1"
        assert args["Metadata"][po.DATABASE_ID_METADATA_KEY] == "db1"
        assert args["Metadata"][po.UPLOAD_ID_METADATA_KEY] == "up1"

    def test_stamp_without_a_listing_head_reads_and_restates_the_headers(self, s3):
        s3c, s3r = s3
        s3c.head_object.return_value = dict(FULL_HEAD)

        assert po.update_s3_object_metadata(KEY, "asset1", "db1", "up1", BUCKET) is True

        s3c.head_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)
        args = _copy_extra_args(s3r)
        assert args["ContentEncoding"] == "gzip"
        assert args["CacheControl"] == "max-age=60"
        assert args["Expires"] == EXPIRES

    def test_object_without_optional_headers_restates_only_its_type(self, s3):
        s3c, s3r = s3
        s3c.head_object.return_value = {"ContentType": "model/gltf-binary", "VersionId": "v1",
                                        "Metadata": {}}
        objects = [{"Key": KEY}]

        po._head_listed_objects(BUCKET, objects)
        po._stamp_output_objects(objects, "asset1", "db1", "up1", BUCKET)

        args = _copy_extra_args(s3r)
        assert args["ContentType"] == "model/gltf-binary"
        for field in ("ContentEncoding", "CacheControl", "ContentDisposition", "ContentLanguage",
                      "Expires"):
            assert field not in args
