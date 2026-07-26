# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""WB5 final-review fix: processWorkflowExecutionOutput reads produced files from the run I/O bucket
(where the pipelines staged them) and tells fileIngestion to copy from there, so a multi-bucket
output asset no longer completes with zero outputs."""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-upload")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-uploads")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po

MOD = "backend.backend.handlers.workflows.sfn.processWorkflowExecutionOutput"


@pytest.mark.unit
def test_process_external_upload_forwards_source_bucket():
    captured = {}

    class _Payload:
        def read(self):
            return json.dumps({"statusCode": 200, "body": json.dumps({"ok": True})}).encode()

    def _invoke(FunctionName, InvocationType, Payload):
        captured["payload"] = json.loads(Payload.decode("utf-8"))
        return {"Payload": _Payload()}

    with patch.object(po, "client") as m_client:
        m_client.invoke.side_effect = _invoke
        po.process_external_upload(
            "upl-1", "asset1", "db1", "assetFile", ["pipelines/p/out/files/model.glb"],
            "pipelines/p/out/files/", {"http": {}}, source_bucket="run-bucket")

    body = json.loads(captured["payload"]["body"])
    assert body["sourceBucket"] == "run-bucket"  # fileIngestion reads temp files from the run bucket


@pytest.mark.unit
def test_process_external_upload_omits_source_bucket_when_unset():
    class _Payload:
        def read(self):
            return json.dumps({"statusCode": 200, "body": json.dumps({"ok": True})}).encode()

    captured = {}

    def _invoke(FunctionName, InvocationType, Payload):
        captured["payload"] = json.loads(Payload.decode("utf-8"))
        return {"Payload": _Payload()}

    with patch.object(po, "client") as m_client:
        m_client.invoke.side_effect = _invoke
        po.process_external_upload(
            "upl-1", "asset1", "db1", "assetFile", ["a/model.glb"], "a/",
            {"http": {}})  # no source_bucket

    body = json.loads(captured["payload"]["body"])
    assert "sourceBucket" not in body  # legacy single-bucket path unaffected


@pytest.mark.unit
def test_process_external_upload_authorizes_as_cross_call():
    """The write-back invokes fileIngestion as a system cross-call so a trigger-launched
    execution (whose stored request context carries no authorizer claims) still authorizes."""
    captured = {}

    class _Payload:
        def read(self):
            return json.dumps({"statusCode": 200, "body": json.dumps({"ok": True})}).encode()

    def _invoke(FunctionName, InvocationType, Payload):
        captured["payload"] = json.loads(Payload.decode("utf-8"))
        return {"Payload": _Payload()}

    with patch.object(po, "client") as m_client:
        m_client.invoke.side_effect = _invoke
        # No change_user_id -> SYSTEM_USER
        po.process_external_upload(
            "upl-1", "asset1", "db1", "assetFile", ["a/model.glb"], "a/", {"http": {}})
    assert captured["payload"]["lambdaCrossCall"]["userName"] == "SYSTEM_USER"

    with patch.object(po, "client") as m_client:
        m_client.invoke.side_effect = _invoke
        # Explicit executing user is preserved for provenance/authorization.
        po.process_external_upload(
            "upl-1", "asset1", "db1", "assetFile", ["a/model.glb"], "a/", {"http": {}},
            change_user_id="alice")
    assert captured["payload"]["lambdaCrossCall"]["userName"] == "alice"
