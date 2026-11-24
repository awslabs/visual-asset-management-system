#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Another asset stored inside the asset's location keeps its files: vamsExecute looks the nested
locations up in the location index once, the gate leaves their keys out of the fingerprint, and
the locations travel to the container through openPipeline and constructPipeline."""
import importlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from miris_upload_fakes import FakeS3

pytestmark = pytest.mark.unit

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

ROOT = "vams/data/a1/"
AUX = "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/"


class IndexTable:
    """An asset table whose location index answers with `rows`, `page_size` rows per page."""

    def __init__(self, rows, page_size=2):
        self.rows, self.page_size, self.queries = rows, page_size, []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        start = int((kwargs.get("ExclusiveStartKey") or {}).get("i", 0))
        page = self.rows[start:start + self.page_size]
        result = {"Items": page}
        if start + self.page_size < len(self.rows):
            result["LastEvaluatedKey"] = {"i": start + self.page_size}
        return result


def _row(location, asset_id="n1", database_id="db1"):
    return {"databaseId": database_id, "assetId": asset_id, "locationBucket": "b", "locationKey": location}


# --- the lookup and the rule ------------------------------------------------------------------


def test_the_lookup_reads_the_location_index_under_the_asset_root_across_pages():
    import assetNesting

    table = IndexTable([
        _row(ROOT, asset_id="a1"),                       # the asset itself
        _row(ROOT, asset_id="same-place"),               # another asset at the same location
        _row(f"{ROOT}parts/", asset_id="n1"),            # a nested folder asset
        _row(f"{ROOT}scans/scan.e57", asset_id="n2"),    # a nested single-file asset
        _row(f"{ROOT}parts/bolt/", asset_id="n3", database_id="db2"),
    ])

    nested = assetNesting.nested_locations(table, "b", ROOT, "db1", "a1")

    assert nested == [f"{ROOT}parts/", f"{ROOT}parts/bolt/", f"{ROOT}scans/scan.e57"]
    first = table.queries[0]
    assert first["IndexName"] == "AssetLocationGSI"
    assert first["KeyConditionExpression"] == "locationBucket = :bucket AND begins_with(locationKey, :root)"
    assert first["ExpressionAttributeValues"] == {":bucket": "b", ":root": ROOT}
    assert len(table.queries) == 3


@pytest.mark.parametrize("key, held", [
    (f"{ROOT}parts/bolt.usd", True),
    (f"{ROOT}parts/", True),
    (f"{ROOT}scans/scan.e57", True),
    (f"{ROOT}scans/scan.e57.previewFile.png", True),
    (f"{ROOT}scans/scan.e57/extra.txt", True),
    (f"{ROOT}scans/scan.e57x", False),
    (f"{ROOT}partsfile.usd", False),
    (f"{ROOT}scene.usda", False),
])
def test_a_key_is_held_by_a_nested_location_by_the_core_rule(key, held):
    import assetNesting

    assert assetNesting.held_by_nested([f"{ROOT}parts/", f"{ROOT}scans/scan.e57"], key) is held


def test_no_nested_location_holds_nothing():
    import assetNesting

    assert assetNesting.held_by_nested([], f"{ROOT}scene.usda") is False


# --- the gate ---------------------------------------------------------------------------------


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".usd,.usda,.usdc,.usdz")
    import mirisUploadGate
    return importlib.reload(mirisUploadGate)


def _claim_fingerprint(gate, s3, nested):
    body = {"assetId": "a1", "databaseId": "db1", "assetBucket": "b", "assetRootS3Key": ROOT,
            "manual": False, "inputOutputS3AssetAuxiliaryFilesPath": AUX,
            "sfnExternalTaskToken": "tok-1"}
    if nested is not None:
        body["nestedLocations"] = nested
    with patch.object(gate, "s3_client", s3), patch.object(gate, "sfn_client"), \
         patch.object(gate.time, "time", return_value=s3.now):
        return gate.lambda_handler({"body": json.dumps(body)}, None)["claimFingerprint"]


def test_the_gate_fingerprint_leaves_out_a_nested_assets_files(gate):
    own, with_nested = FakeS3(), FakeS3()
    own.put_file("b", f"{ROOT}scene.usda", "e-scene")
    with_nested.put_file("b", f"{ROOT}scene.usda", "e-scene")
    with_nested.put_file("b", f"{ROOT}parts/bolt.usd", "e-bolt")

    assert _claim_fingerprint(gate, with_nested, [f"{ROOT}parts/"]) == _claim_fingerprint(gate, own, [])
    # Control: without the nested locations the other asset's file changes the fingerprint.
    assert _claim_fingerprint(gate, FakeS3Copy(with_nested), None) != _claim_fingerprint(gate, own, [])


class FakeS3Copy(FakeS3):
    """A fresh FakeS3 holding the same files as `other` and no claims."""

    def __init__(self, other):
        super().__init__()
        self.objects = {k: v for k, v in other.objects.items() if not k[1].startswith("locks/")}


# --- vamsExecute ------------------------------------------------------------------------------


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("OPEN_PIPELINE_FUNCTION_NAME", "open-fn")
    monkeypatch.setenv("MIRIS_UPLOAD_GATE_FUNCTION_NAME", "gate-fn")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "asset-table")
    import vamsExecuteMirisUpload
    return importlib.reload(vamsExecuteMirisUpload)


def _resolved():
    return {
        "assetId": "a1", "databaseId": "db1",
        "inputS3AssetFilePath": f"s3://b/{ROOT}scene.usda",
        "outputS3AssetFilesPath": f"s3://b/{ROOT}", "outputS3AssetPreviewPath": "",
        "outputS3AssetMetadataPath": "", "inputOutputS3AssetAuxiliaryFilesPath": AUX,
        "inputMetadataS3Location": "", "inputConfigurationS3Location": "s3://b/cfg.json",
        "orchestrationEventPrefix": "", "manifestUsed": True, "inputVersionId": "",
        "inputFiles": [{"assetId": "a1", "bucket": "b", "assetRootS3Key": ROOT}],
    }


def _execute(handler, index_rows):
    table = IndexTable(index_rows, page_size=100)
    table.get_item = MagicMock(return_value={"Item": {"assetName": "Pump", "isDistributable": True}})
    gate_reply = {"StatusCode": 200, "Payload": MagicMock(
        read=lambda: json.dumps({"gate": "proceed", "claimFingerprint": "f" * 64}).encode())}
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("NoSuchKey")
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs", return_value=_resolved()), \
         patch.object(handler, "_asset_table", return_value=table), \
         patch.object(handler, "s3_client", s3), \
         patch.object(handler, "sfn_client") as sfn, \
         patch.object(handler, "lambda_client") as lc:
        lc.invoke.side_effect = [gate_reply, {"StatusCode": 200}]
        response = handler.lambda_handler({"body": json.dumps(
            {"TaskToken": "tok-1", "executingUserName": "SYSTEM_USER"})}, None)
    calls = lc.invoke.call_args_list
    gate_body = json.loads(json.loads(calls[0].kwargs["Payload"].decode())["body"]) if calls else None
    open_payload = json.loads(calls[1].kwargs["Payload"].decode()) if len(calls) > 1 else None
    return response, gate_body, open_payload, sfn


def test_vams_execute_hands_the_nested_locations_to_the_gate_and_the_pipeline(handler):
    response, gate_body, open_payload, _ = _execute(handler, [_row(f"{ROOT}parts/")])

    assert response["statusCode"] == 200
    assert gate_body["nestedLocations"] == [f"{ROOT}parts/"]
    assert open_payload["nestedLocations"] == [f"{ROOT}parts/"]


def test_an_asset_with_no_nested_asset_hands_on_an_empty_list(handler):
    _, gate_body, open_payload, _ = _execute(handler, [])

    assert gate_body["nestedLocations"] == [] and open_payload["nestedLocations"] == []


def test_too_many_nested_locations_fail_the_run_before_the_gate_claims(handler):
    long_name = "n" * 900
    rows = [_row(f"{ROOT}{i}-{long_name}/", asset_id=f"n{i}") for i in range(100)]

    response, gate_body, _, sfn = _execute(handler, rows)

    assert response["statusCode"] == 500
    assert gate_body is None
    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert "nested" in cause and "Miris" in cause


# --- openPipeline and constructPipeline -------------------------------------------------------


@pytest.mark.parametrize("given, expected", [
    ([f"{ROOT}parts/"], [f"{ROOT}parts/"]),
    (None, []),
    ("not-a-list", []),
], ids=["list", "absent", "not-a-list"])
def test_construct_pipeline_puts_the_nested_locations_on_the_container_definition(monkeypatch, given, expected):
    monkeypatch.setenv("MIRIS_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("MIRIS_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:::secret:x")
    import constructPipeline
    construct = importlib.reload(constructPipeline)
    event = {
        "jobName": "job-1", "inputS3AssetFilePath": f"s3://b/{ROOT}scene.usda",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/a1",
        "outputS3AssetFilesPath": "s3://b/pipelines/p/job/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://b/pipelines/p/job/output/E1/metadata/",
        "inputConfigurationS3Location": "s3://b/cfg.json", "assetId": "a1", "databaseId": "db1",
        "assetRootS3Path": f"s3://b/{ROOT}", "mirisAssetName": "Pump",
    }
    if given is not None:
        event["nestedLocations"] = given

    stage = json.loads(construct.lambda_handler(event, None)["definition"][0])["stages"][0]

    assert stage["nestedLocations"] == expected
