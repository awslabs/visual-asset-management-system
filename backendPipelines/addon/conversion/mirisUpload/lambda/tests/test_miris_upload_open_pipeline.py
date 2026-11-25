#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
from unittest.mock import patch

import pytest

from miris_upload_fakes import claim_held_by

pytestmark = pytest.mark.unit

# This module's boto3 clients are constructed at import time from AWS_REGION. Set it
# defensively so this test file does not depend on the ambient shell environment.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def open_pipeline(monkeypatch):
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123456789012:stateMachine:x")
    monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".usd")
    monkeypatch.setenv("ORCHESTRATION_BUS_NAME", "")
    monkeypatch.setenv("STATE_MACHINE_LOG_GROUP_NAME", "")
    monkeypatch.setenv("STATE_MACHINE_LOG_GROUP_ARN", "")
    import importlib
    import openPipeline
    return importlib.reload(openPipeline)


PREFIX = "vams.prod.execution.exec1.pipeline.pe1"


def _registered(open_pipeline):
    with patch.object(open_pipeline, "events_client") as ev:
        open_pipeline.register_sub_execution(
            "my-bus", PREFIX, "arn:aws:states:us-east-1:123456789012:execution:x:y", "sm-arn")
    return ev


@pytest.mark.parametrize("bus, prefix", [
    ("", PREFIX), ("my-bus", ""), ("my-bus", "vams.prod.execution.exec1"),
], ids=["no-bus", "no-prefix", "no-pipeline-marker"])
def test_registration_is_skipped_without_a_bus_or_a_pipeline_prefix(open_pipeline, bus, prefix):
    with patch.object(open_pipeline, "events_client") as ev:
        open_pipeline.register_sub_execution(bus, prefix, "exec-arn", "sm-arn")

    ev.put_events.assert_not_called()


def test_registration_reports_the_sub_execution_without_logs_by_default(open_pipeline):
    ev = _registered(open_pipeline)

    ev.put_events.assert_called_once()
    entry = ev.put_events.call_args.kwargs["Entries"][0]
    assert entry["EventBusName"] == "my-bus"
    assert entry["Source"] == PREFIX
    assert entry["DetailType"] == "pipeline.execution.register"

    detail = json.loads(entry["Detail"])
    assert detail["pipelineExecutionId"] == "pe1"
    assert detail["subExecution"] == {
        "stateMachineArn": "sm-arn",
        "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
        "label": "Miris upload processing",
    }
    assert "logs" not in detail


def test_registration_carries_the_state_machine_and_container_log_sources(open_pipeline, monkeypatch):
    sm_group = "/aws/vendedlogs/VAMSstateMachine-MirisUpload"
    job_group = "/aws/vendedlogs/Pipelines/MirisUploadabc123"
    monkeypatch.setattr(open_pipeline, "STATE_MACHINE_LOG_GROUP_NAME", sm_group)
    monkeypatch.setattr(open_pipeline, "STATE_MACHINE_LOG_GROUP_ARN", f"arn:aws:logs:::log-group:{sm_group}")
    monkeypatch.setattr(open_pipeline, "BATCH_JOB_LOG_GROUP_NAME", job_group)
    monkeypatch.setattr(open_pipeline, "BATCH_JOB_LOG_GROUP_ARN", f"arn:aws:logs:::log-group:{job_group}:*")
    monkeypatch.setattr(open_pipeline, "BATCH_JOB_DEFINITION_NAME", "MirisUploadJobdev_vams1a2b3c")

    detail = json.loads(_registered(open_pipeline).put_events.call_args.kwargs["Entries"][0]["Detail"])

    assert detail["logs"] == [
        {
            "logGroupArn": f"arn:aws:logs:::log-group:{sm_group}",
            "logGroupName": sm_group,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "Miris upload state machine",
        },
        {
            "logGroupArn": f"arn:aws:logs:::log-group:{job_group}:*",
            "logGroupName": job_group,
            "logStreamName": "",
            "logStreamPrefix": "MirisUploadJobdev_vams1a2b3c/default/",
            "stageName": "SubmitMirisUploadBatchJob",
            "sourceType": "batch",
            "label": "SubmitMirisUploadBatchJob container",
        },
    ]
    # The container source needs the job definition name.
    assert open_pipeline.batch_container_log_entry("", open_pipeline.BATCH_STATE_NAME) is None


def test_put_events_failure_is_swallowed(open_pipeline):
    with patch.object(open_pipeline, "events_client") as ev:
        ev.put_events.side_effect = Exception("EventBridge unavailable")
        # Registration is best-effort and never fails the pipeline.
        open_pipeline.register_sub_execution("my-bus", PREFIX, "exec-arn", "sm-arn")


AUX = "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/"
FP = "f" * 64
CLAIM_KEY = f"locks/miris-upload/db1/a1/{FP}.claim"


def _execute_event(**overrides):
    event = {
        "inputS3AssetFilePath": "s3://assets/a1/model.usd",
        "inputOutputS3AssetAuxiliaryFilesPath": AUX,
        "assetId": "a1",
        "claimFingerprint": FP,
        "assetRootS3Path": "s3://assets/vams/data/a1/",
        "databaseId": "db1",
        "sfnExternalTaskToken": "tok-1",
    }
    event.update(overrides)
    return event


@pytest.mark.parametrize("input_path, start_error, status", [
    ("s3://assets/a1/model.obj", None, 400),
    ("s3://assets/a1/", None, 400),
    ("s3://assets/a1/model.usd", Exception("state machine unavailable"), 500),
], ids=["rejected-extension", "folder-input", "start-execution-failure"])
def test_a_run_that_does_not_start_fails_the_task_and_releases_the_claim(open_pipeline, input_path, start_error, status):
    with patch.object(open_pipeline, "sfn") as sfn, patch.object(open_pipeline, "s3_client") as s3:
        sfn.start_execution.side_effect = start_error
        claim_held_by(s3, "tok-1")
        resp = open_pipeline.lambda_handler(_execute_event(inputS3AssetFilePath=input_path), None)

    assert resp["statusCode"] == status
    sfn.send_task_failure.assert_called_once()
    s3.delete_object.assert_called_once_with(Bucket="vams-aux-bucket", Key=CLAIM_KEY)


def test_started_execution_keeps_the_claim_and_forwards_the_claim_and_root(open_pipeline):
    import datetime

    with patch.object(open_pipeline, "sfn") as sfn, \
         patch.object(open_pipeline, "s3_client") as s3:
        sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
            "startDate": datetime.datetime(2026, 8, 14),
        }
        resp = open_pipeline.lambda_handler(_execute_event(), None)

    assert resp["statusCode"] == 200
    s3.delete_object.assert_not_called()
    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    # The execution-ended rule reads the claim fields back off the execution input.
    assert sfn_input["claimFingerprint"] == FP
    assert sfn_input["assetRootS3Path"] == "s3://assets/vams/data/a1/"
    assert sfn_input["inputOutputS3AssetAuxiliaryFilesPath"] == AUX


def test_execution_names_are_unique_and_valid(open_pipeline):
    import re

    names = {open_pipeline.new_job_name() for _ in range(200)}

    # Fan-out starts several executions of this state machine within the same millisecond.
    assert len(names) == 200
    for name in names:
        assert len(name) <= 80
        assert re.fullmatch(r"MirisUpload_\d{8}_\d{6}_\d{3}_[0-9a-f]{8}", name), name


@pytest.mark.parametrize("event_version, expected", [("v-launch", "v-launch"), (None, "")])
def test_the_launch_input_version_is_on_the_state_machine_input(open_pipeline, event_version, expected):
    import datetime

    overrides = {} if event_version is None else {"inputVersionId": event_version}
    with patch.object(open_pipeline, "sfn") as sfn, patch.object(open_pipeline, "s3_client"):
        sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
            "startDate": datetime.datetime(2026, 8, 14),
        }
        open_pipeline.lambda_handler(_execute_event(**overrides), None)

    assert json.loads(sfn.start_execution.call_args.kwargs["input"])["inputVersionId"] == expected


@pytest.mark.parametrize("overrides, expected", [
    ({"manualRun": True, "mirisRootFile": ""}, (True, "")),
    ({"manualRun": False, "mirisRootFile": "/scenes/room.usda"}, (False, "/scenes/room.usda")),
    ({}, (False, "")),
], ids=["manual", "miris-root-file", "absent"])
def test_the_root_choice_is_on_the_state_machine_input(open_pipeline, overrides, expected):
    import datetime

    with patch.object(open_pipeline, "sfn") as sfn, patch.object(open_pipeline, "s3_client"):
        sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
            "startDate": datetime.datetime(2026, 8, 14),
        }
        open_pipeline.lambda_handler(_execute_event(**overrides), None)

    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert (sfn_input["manualRun"], sfn_input["mirisRootFile"]) == expected


@pytest.mark.parametrize("results, expected", [
    ("s3://assets/pipelines/m/job/output/E1/results/",
     "s3://assets/pipelines/m/job/output/E1/results/ambiguous_root.json"),
    ("s3://assets/pipelines/m/job/output/E1/results",
     "s3://assets/pipelines/m/job/output/E1/results/ambiguous_root.json"),
    ("", ""), ("s3://assets/", ""),
], ids=["folder", "no-trailing-slash", "absent", "bucket-only"])
def test_the_run_reports_are_under_the_results_path(open_pipeline, results, expected):
    import datetime

    with patch.object(open_pipeline, "sfn") as sfn, patch.object(open_pipeline, "s3_client"):
        sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
            "startDate": datetime.datetime(2026, 8, 14),
        }
        open_pipeline.lambda_handler(_execute_event(outputS3AssetResultsPath=results), None)

    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert sfn_input["ambiguousRootReportS3Uri"] == expected
    assert sfn_input["preflightWarningsReportS3Uri"] == expected.replace(
        "ambiguous_root.json", "preflight_warnings.txt")


@pytest.mark.parametrize("overrides, expected", [
    ({"nestedLocationsS3Uri": AUX + "nested-locations.json"}, AUX + "nested-locations.json"),
    ({}, ""),
    ({"nestedLocationsS3Uri": ["not", "a", "uri"]}, ""),
], ids=["uri", "absent", "not-a-string"])
def test_the_nested_locations_uri_is_on_the_state_machine_input(open_pipeline, overrides, expected):
    import datetime

    with patch.object(open_pipeline, "sfn") as sfn, patch.object(open_pipeline, "s3_client"):
        sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:x:y",
            "startDate": datetime.datetime(2026, 8, 14),
        }
        open_pipeline.lambda_handler(_execute_event(**overrides), None)

    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert sfn_input["nestedLocationsS3Uri"] == expected
    assert "nestedLocations" not in sfn_input
