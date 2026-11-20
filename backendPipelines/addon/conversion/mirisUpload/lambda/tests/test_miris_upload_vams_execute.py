#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from miris_upload_fakes import claim_held_by

pytestmark = pytest.mark.unit

# This module's boto3 clients are constructed at import time from AWS_REGION. Set it
# defensively so this test file does not depend on the ambient shell environment.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

FP = "f" * 64


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("OPEN_PIPELINE_FUNCTION_NAME", "open-fn")
    monkeypatch.setenv("MIRIS_UPLOAD_GATE_FUNCTION_NAME", "gate-fn")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "asset-table")
    import importlib
    import vamsExecuteMirisUpload
    return importlib.reload(vamsExecuteMirisUpload)


def _resolved(**overrides):
    resolved = {
        "assetId": "a1",
        "databaseId": "db1",
        "inputS3AssetFilePath": "s3://b/vams/data/a1/model.usd",
        "outputS3AssetFilesPath": "s3://b/vams/data/a1/",
        "outputS3AssetPreviewPath": "",
        "outputS3AssetMetadataPath": "",
        "inputOutputS3AssetAuxiliaryFilesPath":
            "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/",
        "inputMetadataS3Location": "",
        "inputConfigurationS3Location": "s3://b/cfg.json",
        "orchestrationEventPrefix": "",
        "inputFiles": [{"assetId": "a1", "bucket": "b", "assetRootS3Key": "vams/data/a1"}],
        "manifestUsed": True,
        "inputVersionId": "",
    }
    resolved.update(overrides)
    return resolved


def _asset_table_mock(asset_name="Pump Housing"):
    table = MagicMock()
    table.get_item.return_value = {"Item": {"assetName": asset_name}}
    return table


def _gate_says(gate="proceed", fingerprint=FP):
    return {
        "StatusCode": 200,
        "Payload": MagicMock(read=lambda: json.dumps(
            {"gate": gate, "claimFingerprint": fingerprint}).encode()),
    }


def _configuration(body):
    """An S3 client whose rendered configuration file holds `body` (a dict, raw text, or None)."""
    s3 = MagicMock()
    if body is None:
        s3.get_object.side_effect = Exception("NoSuchKey")
    else:
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        s3.get_object.side_effect = lambda **_: {"Body": io.BytesIO(raw)}
    return s3


def _run(handler, resolved=None, s3=None, executing_user="SYSTEM_USER", gate=None,
         open_result=None):
    """Run the handler; returns (response, gate body, open payload or None, lambda invoke mock)."""
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs",
                      return_value=resolved or _resolved()), \
         patch.object(handler, "_asset_table", return_value=_asset_table_mock()), \
         patch.object(handler, "s3_client", s3 or _configuration(None)), \
         patch.object(handler, "sfn_client"), \
         patch.object(handler, "lambda_client") as lc:
        lc.invoke.side_effect = [gate or _gate_says(), open_result or {"StatusCode": 200}]
        resp = handler.lambda_handler({"body": json.dumps(
            {"TaskToken": "tok-1", "executingUserName": executing_user})}, None)
    calls = lc.invoke.call_args_list
    gate_body = json.loads(json.loads(calls[0].kwargs["Payload"].decode())["body"]) if calls else None
    open_payload = json.loads(calls[1].kwargs["Payload"].decode()) if len(calls) > 1 else None
    return resp, gate_body, open_payload, lc.invoke


def test_missing_task_token_reports_nothing_and_500s(handler):
    # No token to report against, so the handler must not raise on the way out.
    resp = handler.lambda_handler({"body": json.dumps({"assetId": "a1"})}, None)
    assert resp["statusCode"] == 500


def test_manifest_failure_sends_task_failure_and_releases_nothing(handler):
    with patch.object(handler, "sfn_client") as sfn, \
         patch.object(handler, "s3_client") as s3, \
         patch.object(handler.manifestHelper, "resolve_pipeline_inputs",
                      side_effect=Exception("manifest unreadable")):
        resp = handler.lambda_handler(
            {"body": json.dumps({"TaskToken": "tok-1"})}, None)

    assert resp["statusCode"] == 500
    sfn.send_task_failure.assert_called_once()
    assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-1"
    s3.delete_object.assert_not_called()


def test_the_gate_and_the_pipeline_receive_the_asset_root_name_and_claim(handler):
    resp, gate_body, open_payload, _ = _run(handler)

    assert resp["statusCode"] == 200
    assert (gate_body["assetBucket"], gate_body["assetRootS3Key"]) == ("b", "vams/data/a1/")
    assert gate_body["mirisAssetName"] == "Pump Housing"
    # openPipeline needs the fingerprint to release the claim, and the container lists the root.
    assert open_payload["claimFingerprint"] == FP
    assert open_payload["assetRootS3Path"] == "s3://b/vams/data/a1/"
    assert open_payload["databaseId"] == "db1"


def test_gate_skip_short_circuits(handler):
    s3 = _configuration(None)
    resp, _, open_payload, invoke = _run(handler, s3=s3, gate=_gate_says("skip", ""))

    assert resp["statusCode"] == 200
    # Only the gate was invoked; openPipeline must not run, and the winner's claim stays.
    assert invoke.call_count == 1
    assert open_payload is None
    s3.delete_object.assert_not_called()


def test_failure_after_claim_releases_it(handler):
    s3 = claim_held_by(MagicMock(), "tok-1")
    resp, _, _, _ = _run(handler, s3=s3, open_result=Exception("openPipeline unreachable"))

    assert resp["statusCode"] == 500
    s3.delete_object.assert_called_once_with(
        Bucket="vams-aux-bucket", Key=f"locks/miris-upload/db1/a1/{FP}.claim")


@pytest.mark.parametrize("overrides", [
    {"inputFiles": []},
    {"inputFiles": [{"assetId": "a1", "bucket": "b", "assetRootS3Key": ""}]},
    {"databaseId": ""},
], ids=["no-input-file", "no-root", "no-database"])
def test_a_run_without_an_asset_root_or_identity_fails_before_the_gate(handler, overrides):
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs",
                      return_value=_resolved(**overrides)), \
         patch.object(handler, "_asset_table", return_value=_asset_table_mock()), \
         patch.object(handler, "s3_client", _configuration(None)), \
         patch.object(handler, "sfn_client") as sfn, \
         patch.object(handler, "lambda_client") as lc:
        resp = handler.lambda_handler({"body": json.dumps({"TaskToken": "tok-1"})}, None)

    assert resp["statusCode"] == 500
    lc.invoke.assert_not_called()
    sfn.send_task_failure.assert_called_once()


# --- manual runs ------------------------------------------------------------------------------


@pytest.mark.parametrize("configuration,executing_user,manual", [
    ({"triggerType": "Manual"}, "SYSTEM_USER", True),
    ({"triggerType": "File-Upload"}, "alice", False),
    (None, "SYSTEM_USER", False),
    (None, "alice", True),
    ({"triggerType": "{{triggerType}}"}, "SYSTEM_USER", False),
    ({"triggerType": ""}, "alice", True),
    ("not json", "SYSTEM_USER", False),
], ids=["rendered-manual", "rendered-file-upload", "trigger-user", "user",
        "unrendered-tag", "blank-trigger-type", "malformed"])
def test_the_manual_flag_reaches_the_gate(handler, configuration, executing_user, manual):
    _, gate_body, _, _ = _run(handler, s3=_configuration(configuration), executing_user=executing_user)

    assert gate_body["manual"] is manual


# --- same-Region only -------------------------------------------------------------------------


def _regional(input_region="", output_region=""):
    return _resolved(
        inputFiles=[{"assetId": "a1", "bucket": "b", "assetRootS3Key": "vams/data/a1",
                     "bucketRegion": input_region}],
        outputBucketRegion=output_region)


def _refused(handler, monkeypatch, deployment_region, resolved):
    """Run the handler on `resolved`; returns (response, send_task_failure mock, invoke mock, s3)."""
    monkeypatch.setenv("AWS_REGION", deployment_region)
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs", return_value=resolved), \
         patch.object(handler, "_asset_table", return_value=_asset_table_mock()), \
         patch.object(handler, "s3_client", _configuration(None)) as s3, \
         patch.object(handler, "sfn_client") as sfn, \
         patch.object(handler, "lambda_client") as lc:
        resp = handler.lambda_handler({"body": json.dumps({"TaskToken": "tok-1"})}, None)
    return resp, sfn.send_task_failure, lc.invoke, s3


@pytest.mark.parametrize("placement", ["input", "output", "both"])
def test_a_cross_region_bucket_fails_the_task_with_the_whole_miris_reason(handler, monkeypatch, placement):
    resolved = _regional("eu-west-1" if placement in ("input", "both") else "",
                         "eu-west-1" if placement in ("output", "both") else "")
    resp, send_failure, invoke, s3 = _refused(handler, monkeypatch, "us-east-1", resolved)

    assert resp["statusCode"] == 500
    # Rejected before the gate claims anything, so there is no claim to release and nothing starts.
    invoke.assert_not_called()
    s3.delete_object.assert_not_called()
    assert send_failure.call_args.kwargs["taskToken"] == "tok-1"

    cause = send_failure.call_args.kwargs["cause"]
    # The whole reason reaches the workflow: the 256-character cut-off never truncates it.
    assert cause == handler.same_region_reason(resolved, "us-east-1")
    assert len(cause) < 256
    assert "must be in us-east-1" in cause
    for bucket in ("input", "output"):
        assert (f"the {bucket} bucket is in eu-west-1" in cause) == (placement in (bucket, "both"))


def test_same_region_buckets_reach_the_gate(handler, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    resp, gate_body, open_payload, _ = _run(handler, resolved=_regional("us-east-1", "us-east-1"))

    assert resp["statusCode"] == 200
    assert gate_body["assetId"] == "a1"
    assert open_payload is not None


@pytest.mark.parametrize("version", ["v-launch", "null", ""])
def test_the_launch_input_version_reaches_the_pipeline(handler, version):
    _, _, open_payload, _ = _run(handler, resolved=_resolved(inputVersionId=version))

    assert open_payload["inputVersionId"] == version


# --- the root the container uses --------------------------------------------------------------


def _metadata_envelope(root_file):
    asset_metadata = {} if root_file is None else {"miris_root_file": root_file}
    return {"schemaVersion": 2, "metadata": {"schemaVersion": 2, "assets": [{
        "databaseId": "db1", "assetId": "a1",
        "files": [{"fileKey": "/", "metadata": asset_metadata}]}]}}


@pytest.mark.parametrize("trigger_type, root_file, expected", [
    ("File-Upload", "/scenes/room.usda", (False, "/scenes/room.usda")),
    ("File-Upload", None, (False, "")),
    ("Manual", "/scenes/room.usda", (True, "")),
], ids=["automatic-with-miris-root-file", "automatic-without", "manual-ignores-it"])
def test_the_manual_flag_and_miris_root_file_reach_the_pipeline(handler, trigger_type, root_file, expected):
    bodies = {"cfg.json": {"triggerType": trigger_type}, "metadata.json": _metadata_envelope(root_file)}
    s3 = MagicMock()
    s3.get_object.side_effect = lambda Bucket, Key: {"Body": io.BytesIO(json.dumps(bodies[Key]).encode())}

    _, _, open_payload, _ = _run(handler, s3=s3, resolved=_resolved(
        inputConfigurationS3Location="s3://b/cfg.json", inputMetadataS3Location="s3://b/metadata.json"))

    assert (open_payload["manualRun"], open_payload["mirisRootFile"]) == expected


def test_the_bundle_gives_the_step_the_asset_metadata():
    schema_dir = os.path.join(os.path.dirname(__file__), "..", "..", "vamsSchema")
    for name in ("pipeline.json", "workflow.json"):
        with open(os.path.join(schema_dir, name), encoding="utf-8") as f:
            assert json.load(f)["systemConfig"]["metadataInputs"]["assetMetadata"] is True, name


def test_an_unreadable_input_metadata_file_fails_the_run_before_the_gate(handler):
    def get_object(Bucket, Key):
        if Key == "metadata.json":
            raise Exception("AccessDenied")
        return {"Body": io.BytesIO(json.dumps({"triggerType": "File-Upload"}).encode())}
    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    resolved = _resolved(inputConfigurationS3Location="s3://b/cfg.json",
                         inputMetadataS3Location="s3://b/metadata.json")
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs", return_value=resolved), \
         patch.object(handler, "_asset_table", return_value=_asset_table_mock()), \
         patch.object(handler, "s3_client", s3), \
         patch.object(handler, "sfn_client") as sfn, \
         patch.object(handler, "lambda_client") as lc:
        resp = handler.lambda_handler({"body": json.dumps({"TaskToken": "tok-1"})}, None)

    assert resp["statusCode"] == 500
    lc.invoke.assert_not_called()
    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert cause.startswith("The run's input metadata file could not be read") and "miris_root_file" in cause


def test_the_results_path_reaches_the_pipeline(handler):
    results = "s3://b/vams/data/pipelines/m/job/output/E1/results/"
    _, _, open_payload, _ = _run(handler, resolved=_resolved(outputS3AssetResultsPath=results))

    assert open_payload["outputS3AssetResultsPath"] == results
