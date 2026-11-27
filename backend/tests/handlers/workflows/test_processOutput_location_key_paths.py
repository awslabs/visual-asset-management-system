# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow output written into a subfolder named like the asset's location lands in that subfolder (#1943).

The output write-back sent each asset file's path without a leading '/'. The upload handler reads a value
without one as the full key when it is one of the asset's keys, so an output a pipeline wrote at ``A1/out.las``
for an asset stored at ``A1/`` replaced the root file ``A1/out.las`` instead of writing ``A1/A1/out.las``. The
file-level metadata output did the same through the metadata service. Both now send the path with a leading
'/', which the file routes read as a path in the asset; the per-file results are read back without it, so the
recorded outputs keep their keys.
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

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

PREFIX = "pipelines/p1/JOB/output/E1/files/"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"


def _ok_response(body):
    return {"Payload": MagicMock(read=MagicMock(return_value=json.dumps(
        {"statusCode": 200, "body": json.dumps(body)}).encode("utf-8")))}


def _sent_files(ingest):
    return json.loads(ingest.call_args.args[0]["body"])["files"]


@pytest.mark.unit
class TestTheFileWriteBackSendsAPathInTheAsset:
    @pytest.mark.parametrize("staged,sent", [
        (PREFIX + "A1/out.las", "/A1/out.las"),
        (PREFIX + "out.las", "/out.las"),
        (PREFIX + "sub/out.las", "/sub/out.las"),
    ])
    def test_an_asset_file_is_sent_with_a_leading_slash(self, staged, sent):
        ingest = MagicMock(return_value=_ok_response({}))
        with patch.object(po, "_lambda_file_ingestion", ingest):
            po.process_external_upload("up-1", "A1", "db1", "assetFile", [staged], PREFIX, {"http": {}})
        assert _sent_files(ingest) == [{"relativeKey": sent, "tempKey": staged}]

    def test_a_preview_keeps_its_basename(self):
        ingest = MagicMock(return_value=_ok_response({}))
        staged = "pipelines/p1/JOB/output/E1/preview/thumb.png"
        with patch.object(po, "_lambda_file_ingestion", ingest):
            po.process_external_upload("up-1", "A1", "db1", "assetPreview", [staged],
                                       "pipelines/p1/JOB/output/E1/preview/", {"http": {}})
        assert _sent_files(ingest) == [{"relativeKey": "thumb.png", "tempKey": staged}]

    def test_the_results_are_read_back_by_the_outputs_own_keys(self):
        """The completion echoes the '/'-form; the recorded outputs are keyed without it."""
        result = {"overallSuccess": False, "fileResults": [
            {"relativeKey": "/A1/out.las", "success": True},
            {"relativeKey": "/sub/out.las", "success": True},
            {"relativeKey": "/bad.las", "success": False},
            {"relativeKey": "thumb.png", "success": True}]}
        assert po._ingestion_outcome(result) == (False, {"A1/out.las", "sub/out.las", "thumb.png"})

    def test_the_recorded_descriptor_matches_the_result_key(self):
        descriptors = po._collect_output_descriptors(
            {"Contents": [{"Key": PREFIX + "A1/out.las", "Size": 1, "VersionId": "v1"}]}, "file", PREFIX, "bkt")
        _, ingested = po._ingestion_outcome({"fileResults": [{"relativeKey": "/A1/out.las", "success": True}]})
        assert [d["relativeFilePath"] for d in descriptors] == ["A1/out.las"]
        assert descriptors[0]["relativeFilePath"] in ingested


@pytest.mark.unit
class TestTheMetadataWriteBackSendsAPathInTheAsset:
    @pytest.mark.parametrize("file_path,sent", [("A1/out.las", "/A1/out.las"), ("out.las", "/out.las")])
    def test_the_file_metadata_request_carries_the_path_with_a_leading_slash(self, file_path, sent):
        document = json.dumps({"type": "metadata", "metadata": [
            {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]}).encode("utf-8")
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=document))}
        metadata_service = MagicMock(return_value=_ok_response({"message": "ok"}))
        with patch.object(po, "s3c", s3), patch.object(po, "_lambda_metadata_service", metadata_service):
            applied = po.process_metadata_file("bkt", METADATA_PREFIX + file_path + ".metadata.json",
                                               METADATA_PREFIX, "db1", "A1", file_path, "metadata", {"http": {}})
        assert applied is not None
        assert json.loads(metadata_service.call_args.args[0]["body"])["filePath"] == sent

    def test_asset_level_metadata_carries_no_file_path(self):
        document = json.dumps({"type": "metadata", "metadata": [
            {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]}).encode("utf-8")
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=document))}
        metadata_service = MagicMock(return_value=_ok_response({"message": "ok"}))
        with patch.object(po, "s3c", s3), patch.object(po, "_lambda_metadata_service", metadata_service):
            po.process_metadata_file("bkt", METADATA_PREFIX + "asset.metadata.json", METADATA_PREFIX,
                                     "db1", "A1", None, "metadata", {"http": {}})
        assert "filePath" not in json.loads(metadata_service.call_args.args[0]["body"])
