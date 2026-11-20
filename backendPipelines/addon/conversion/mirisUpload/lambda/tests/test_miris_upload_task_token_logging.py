#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""No Miris upload handler hands a task token (a bearer credential) to its logger, on any success
or failure route: each handler's logger is replaced with a recorder that is searched for the token."""
import datetime
import importlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.unit

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

TOKEN = "AQCEAAAAKgAAAAMAAAAAAAAAAmirisTokenUnderTest0123456789"
AUX = "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/"


class RecordingLogger:
    """Records every call's positional and keyword arguments as text."""

    def __init__(self):
        self.lines = []

    def _record(self, *args, **kwargs):
        self.lines.append(repr(args) + repr(kwargs) + "".join(str(a) for a in args))

    info = warning = error = exception = debug = _record

    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def recorder():
    return RecordingLogger()


def _load(name, monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(importlib.import_module(name))


def _assert_token_not_logged(recorder):
    assert recorder.lines, "control: the handler logged nothing, so the search below is vacuous"
    assert TOKEN not in recorder.text()


def _open_event(**overrides):
    event = {
        "inputS3AssetFilePath": "s3://assets/a1/model.usd",
        "inputOutputS3AssetAuxiliaryFilesPath": AUX,
        "outputS3AssetMetadataPath": "s3://assets/pipelines/p/j/output/E1/metadata/",
        "assetId": "a1",
        "claimFingerprint": "fp1",
        "databaseId": "db1",
        "sfnExternalTaskToken": TOKEN,
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
    }
    event.update(overrides)
    return event


@pytest.mark.parametrize("route", ["started", "bad_extension", "folder", "start_failed"])
def test_open_pipeline_never_logs_the_token(monkeypatch, recorder, route):
    mod = _load(
        "openPipeline", monkeypatch,
        STATE_MACHINE_ARN="arn:aws:states:us-east-1:123456789012:stateMachine:x",
        ALLOWED_INPUT_FILEEXTENSIONS=".usd,.usdz",
        ORCHESTRATION_BUS_NAME="bus",
    )
    event = {
        "started": _open_event(),
        "bad_extension": _open_event(inputS3AssetFilePath="s3://assets/a1/model.us"),
        "folder": _open_event(inputS3AssetFilePath="s3://assets/a1/"),
        "start_failed": _open_event(),
    }[route]
    with patch.object(mod, "logger", recorder), patch.object(mod, "sfn") as sfn, \
            patch.object(mod, "s3_client"), patch.object(mod, "events_client"):
        sfn.start_execution.return_value = {
            "executionArn": "arn", "startDate": datetime.datetime(2026, 1, 1)}
        if route == "start_failed":
            sfn.start_execution.side_effect = Exception("state machine unavailable")
        mod.lambda_handler(event, None)

    _assert_token_not_logged(recorder)


@pytest.mark.parametrize("route", ["success", "failure", "timed_out", "closed_token"])
def test_pipeline_end_never_logs_the_token(monkeypatch, recorder, route):
    mod = _load("pipelineEnd", monkeypatch)
    claim = {"assetId": "a1", "claimFingerprint": "fp1", "inputOutputS3AssetAuxiliaryFilesPath": AUX}
    status_event = {
        "detail-type": "Step Functions Execution Status Change",
        "detail": {"status": "TIMED_OUT", "input": json.dumps({"externalSfnTaskToken": TOKEN, **claim})},
    }
    event = {
        "success": {"externalSfnTaskToken": TOKEN, "status": "success", **claim},
        "failure": {"externalSfnTaskToken": TOKEN, "status": "failed", **claim},
        "timed_out": status_event,
        "closed_token": status_event,
    }[route]
    with patch.object(mod, "logger", recorder), patch.object(mod, "sfn") as sfn, \
            patch.object(mod, "s3_client"):
        if route == "closed_token":
            sfn.send_task_failure.side_effect = ClientError(
                {"Error": {"Code": "TaskTimedOut", "Message": f"Task {TOKEN} timed out"}},
                "SendTaskFailure")
        mod.lambda_handler(event, None)

    _assert_token_not_logged(recorder)


@pytest.mark.parametrize("route", ["proceed", "manifest_failed"])
def test_vams_execute_never_logs_the_token(monkeypatch, recorder, route):
    mod = _load(
        "vamsExecuteMirisUpload", monkeypatch,
        OPEN_PIPELINE_FUNCTION_NAME="open-fn",
        MIRIS_UPLOAD_GATE_FUNCTION_NAME="gate-fn",
        ASSET_STORAGE_TABLE_NAME="asset-table",
    )
    resolved = {k: "" for k in (
        "inputS3AssetFilePath", "outputS3AssetFilesPath", "outputS3AssetPreviewPath",
        "outputS3AssetMetadataPath", "inputMetadataS3Location", "inputConfigurationS3Location",
        "orchestrationEventPrefix")}
    resolved.update(assetId="a1", databaseId="db1",
                    inputFiles=[{"assetId": "a1", "bucket": "assets", "assetRootS3Key": "a1/"}],
                    inputOutputS3AssetAuxiliaryFilesPath=AUX, manifestUsed=True)
    table = MagicMock()
    table.get_item.return_value = {"Item": {"assetName": "Pump"}}
    resolve = (MagicMock(side_effect=Exception("manifest unreadable")) if route == "manifest_failed"
               else MagicMock(return_value=resolved))
    with patch.object(mod, "logger", recorder), \
            patch.object(mod.manifestHelper, "resolve_pipeline_inputs", resolve), \
            patch.object(mod, "_asset_table", return_value=table), \
            patch.object(mod, "sfn_client"), patch.object(mod, "s3_client"), \
            patch.object(mod, "lambda_client") as lc:
        lc.invoke.return_value = {
            "StatusCode": 200,
            "Payload": MagicMock(read=lambda: json.dumps({"gate": "proceed"}).encode()),
        }
        mod.lambda_handler({"body": json.dumps({"TaskToken": TOKEN})}, None)

    _assert_token_not_logged(recorder)


def test_gate_never_logs_the_token(monkeypatch, recorder):
    mod = _load("mirisUploadGate", monkeypatch)
    event = {"body": json.dumps({
        "assetId": "a1", "databaseId": "db1", "assetBucket": "assets", "assetRootS3Key": "a1/",
        "inputOutputS3AssetAuxiliaryFilesPath": AUX, "sfnExternalTaskToken": TOKEN})}
    with patch.object(mod, "logger", recorder), patch.object(mod, "s3_client") as s3, \
            patch.object(mod, "sfn_client"):
        s3.exceptions.ClientError = ClientError
        s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "a1/model.usd", "ETag": '"e1"'}]}]
        s3.put_object.side_effect = ClientError(
            {"Error": {"Code": "PreconditionFailed", "Message": "exists"}}, "PutObject")
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"owner": "someone-else"}')}
        mod.lambda_handler(event, None)

    _assert_token_not_logged(recorder)
