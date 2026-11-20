#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
import pytest

pytestmark = pytest.mark.unit

# This module's boto3 clients are constructed at import time from AWS_REGION. Set it
# defensively so this test file does not depend on the ambient shell environment.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def construct(monkeypatch):
    monkeypatch.setenv("MIRIS_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("MIRIS_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:::secret:x")
    import importlib
    import constructPipeline
    return importlib.reload(constructPipeline)


def _event(**overrides):
    event = {
        "jobName": "job-1",
        "inputS3AssetFilePath": "s3://assets/a1/sub/model.usd",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/a1",
        "outputS3AssetFilesPath": "s3://assets/pipelines/p/job/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://assets/pipelines/p/job/output/E1/metadata/",
        "inputConfigurationS3Location": "s3://assets/cfg.json",
        "assetId": "a1",
        "databaseId": "db1",
        "assetRootS3Path": "s3://assets/a1/",
        "mirisAssetName": "Pump Housing",
    }
    event.update(overrides)
    return event


def _stage(result):
    return json.loads(result["definition"][0])["stages"][0]


def test_definition_carries_the_stage_inputs(construct, monkeypatch):
    monkeypatch.setattr(construct, "ALLOWED_INPUT_FILEEXTENSIONS", ".USD, .usdz")
    stage = _stage(construct.lambda_handler(_event(), None))

    assert stage["mirisAssetName"] == "Pump Housing"
    assert stage["inputConfigurationS3Location"] == "s3://assets/cfg.json"
    assert stage["assetId"] == "a1"
    assert stage["rootExtensions"] == [".usd", ".usdz"]
    assert stage["outputMetadataFiles"] == {"bucketName": "assets", "objectDir": "pipelines/p/job/output/E1/metadata/"}
    assert stage["auxiliaryBucketName"] == "aux"
    # The container writes nothing to the files output path.
    assert "outputFiles" not in stage


@pytest.mark.parametrize("fingerprint", ["fp1", None])
def test_claim_fields_are_carried_to_the_failure_branch(construct, fingerprint):
    # The state machine's failure branch reads these JSONPaths off the construct result.
    overrides = {"claimFingerprint": fingerprint} if fingerprint else {}
    result = construct.lambda_handler(_event(**overrides), None)

    assert result["status"] == "STARTING"
    assert (result["databaseId"], result["assetId"]) == ("db1", "a1")
    assert result["claimFingerprint"] == (fingerprint or "")
    assert result["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/a1"


@pytest.mark.parametrize("root_uri,expected", [
    ("s3://assets/a1/", {"bucketName": "assets", "objectDir": "a1/"}),
    ("s3://assets/vams/data/a1", {"bucketName": "assets", "objectDir": "vams/data/a1/"}),
])
def test_definition_carries_the_resolved_asset_root(construct, root_uri, expected):
    assert _stage(construct.lambda_handler(_event(assetRootS3Path=root_uri), None))["assetRoot"] == expected


@pytest.mark.parametrize("field", ["assetRootS3Path", "outputS3AssetMetadataPath"])
@pytest.mark.parametrize("value", [None, "", "s3://assets/", "assets/a1/"])
def test_a_missing_or_unusable_s3_path_fails_before_batch_submit(construct, field, value):
    event = _event()
    if value is None:
        del event[field]
    else:
        event[field] = value

    with pytest.raises(ValueError, match=field):
        construct.lambda_handler(event, None)


def test_metadata_output_prefix_is_normalized_to_a_folder(construct):
    stage = _stage(construct.lambda_handler(
        _event(outputS3AssetMetadataPath="s3://assets/pipelines/p/job/output/E1/metadata"), None))

    assert stage["outputMetadataFiles"]["objectDir"] == "pipelines/p/job/output/E1/metadata/"


def test_the_container_definition_carries_no_task_token(construct):
    # The definition is the Batch job's command line, readable by anyone who can describe the job,
    # and the container never reports to Step Functions itself.
    result = construct.lambda_handler(_event(externalSfnTaskToken="tok-secret-1"), None)

    assert "tok-secret-1" not in result["definition"][0]
    # The state machine's end tasks still read it off the state.
    assert result["externalSfnTaskToken"] == "tok-secret-1"


@pytest.mark.parametrize("event_version, expected", [("v-launch", "v-launch"), ("null", "null"), (None, "")])
def test_the_launch_input_version_is_on_the_trigger_input(construct, event_version, expected):
    overrides = {} if event_version is None else {"inputVersionId": event_version}
    stage = _stage(construct.lambda_handler(_event(**overrides), None))

    assert stage["triggerInput"]["versionId"] == expected


@pytest.mark.parametrize("overrides, expected", [
    ({"manualRun": True, "mirisRootFile": ""}, (True, "")),
    ({"manualRun": False, "mirisRootFile": "/scenes/room.usda"}, (False, "/scenes/room.usda")),
    ({"manualRun": "true"}, (False, "")),
    ({}, (False, "")),
], ids=["manual", "miris-root-file", "non-boolean", "absent"])
def test_the_root_choice_is_on_the_container_definition(construct, overrides, expected):
    stage = _stage(construct.lambda_handler(_event(**overrides), None))

    assert (stage["manualRun"], stage["mirisRootFile"]) == expected


@pytest.mark.parametrize("uri, location", [
    ("s3://assets/pipelines/m/r/ambiguous_root.json",
     {"bucketName": "assets", "objectKey": "pipelines/m/r/ambiguous_root.json"}),
    ("", {}), ("s3://assets", {}),
], ids=["set", "absent", "no-key"])
def test_the_report_location_reaches_the_container_and_the_end_task(construct, uri, location):
    result = construct.lambda_handler(_event(ambiguousRootReportS3Uri=uri), None)

    assert _stage(result)["ambiguousRootReport"] == location
    # Both end tasks read it off the state.
    assert result["ambiguousRootReportS3Uri"] == uri


@pytest.mark.parametrize("uri, location", [
    ("s3://assets/pipelines/m/r/preflight_warnings.txt",
     {"bucketName": "assets", "objectKey": "pipelines/m/r/preflight_warnings.txt"}),
    ("", {}), ("s3://assets", {}),
], ids=["set", "absent", "no-key"])
def test_the_preflight_warnings_location_reaches_the_container(construct, uri, location):
    stage = _stage(construct.lambda_handler(_event(preflightWarningsReportS3Uri=uri), None))

    assert stage["preflightWarningsReport"] == location
