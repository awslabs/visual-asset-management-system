#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Another asset stored inside the asset's location keeps its files: vamsExecute looks the nested
locations up in the location index once and writes them to an object under the run's auxiliary
path; the gate and the container read them from there, and only the object's URI travels through
openPipeline, constructPipeline and the job's command."""
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
# The run's nested-locations object, under the pipeline's own prefix and keyed by the execution id.
NESTED_KEY = "pipelines/miris-upload/nested-locations/exec-aaaa1111.json"
NESTED = "s3://vams-aux-bucket/" + NESTED_KEY


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



class EndlessIndex(IndexTable):
    """A location index that always reports a further page."""

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": [], "LastEvaluatedKey": {"i": len(self.queries)}}


def test_the_walk_stops_only_when_a_page_has_no_last_evaluated_key():
    import assetNesting

    class EmptyKeyThenDone(IndexTable):
        def query(self, **kwargs):
            self.queries.append(kwargs)
            if len(self.queries) == 1:
                return {"Items": [_row(f"{ROOT}a/")], "LastEvaluatedKey": {}}
            return {"Items": [_row(f"{ROOT}b/", asset_id="n2")]}

    table = EmptyKeyThenDone([])
    assert assetNesting.nested_locations(table, "b", ROOT, "db1", "a1") == [f"{ROOT}a/", f"{ROOT}b/"]
    assert len(table.queries) == 2


def test_a_walk_past_the_page_limit_fails_instead_of_returning_part_of_the_list():
    import assetNesting

    table = EndlessIndex([])
    with pytest.raises(Exception) as raised:
        assetNesting.nested_locations(table, "b", ROOT, "db1", "a1")

    assert len(table.queries) == assetNesting.MAX_NESTED_LOCATION_PAGES == 50
    assert "nested" in str(raised.value) and "Miris" in str(raised.value)


def test_no_nested_location_writes_no_object():
    import assetNesting

    s3 = FakeS3()
    assert assetNesting.write_nested_locations(s3, AUX, []) == ""
    assert s3.objects == {}


def test_the_nested_locations_round_trip_through_the_runs_auxiliary_path():
    import assetNesting

    s3 = FakeS3()
    uri = assetNesting.write_nested_locations(s3, AUX, [f"{ROOT}parts/"])

    assert uri == NESTED
    assert assetNesting.read_nested_locations(s3, uri) == [f"{ROOT}parts/"]
    assert assetNesting.read_nested_locations(s3, "") == []



@pytest.mark.parametrize("aux", [
    "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/",
    "s3://vams-aux-bucket/pipelines/my-custom-job/exec-aaaa1111/",
    "s3://vams-aux-bucket/pipelines/my-custom-job/exec-aaaa1111",
], ids=["registered-workflow", "workflow-job-name", "no-trailing-slash"])
def test_the_object_sits_under_the_pipelines_own_prefix_whatever_the_job_name(aux):
    import assetNesting

    s3 = FakeS3()
    assert assetNesting.write_nested_locations(s3, aux, [f"{ROOT}parts/"]) == NESTED
    assert list(s3.objects) == [("vams-aux-bucket", NESTED_KEY)]


@pytest.mark.parametrize("aux", ["s3://vams-aux-bucket/", "s3://vams-aux-bucket/pipelines/x/../", "not-a-uri"],
                         ids=["no-execution", "dot-dot", "not-s3"])
def test_an_auxiliary_path_without_an_execution_id_is_refused(aux):
    import assetNesting

    with pytest.raises(Exception):
        assetNesting.write_nested_locations(FakeS3(), aux, [f"{ROOT}parts/"])

@pytest.mark.parametrize("body", [b"not json", b'{"nestedLocations": "x"}', b"[1]"])
def test_a_nested_locations_object_that_is_not_a_list_of_locations_is_refused(body):
    import assetNesting

    s3 = FakeS3()
    s3.objects[("vams-aux-bucket", NESTED_KEY)] = (body, '"e"')
    with pytest.raises(Exception):
        assetNesting.read_nested_locations(s3, NESTED)

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
    import assetNesting

    body = {"assetId": "a1", "databaseId": "db1", "assetBucket": "b", "assetRootS3Key": ROOT,
            "manual": False, "inputOutputS3AssetAuxiliaryFilesPath": AUX,
            "sfnExternalTaskToken": "tok-1"}
    if nested is not None:
        body["nestedLocationsS3Uri"] = assetNesting.write_nested_locations(s3, AUX, nested)
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
        self.objects = {k: v for k, v in other.objects.items()
                        if not k[1].startswith("locks/") and not k[1].startswith("pipelines/miris-upload/nested-locations/")}


# --- vamsExecute ------------------------------------------------------------------------------


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("OPEN_PIPELINE_FUNCTION_NAME", "open-fn")
    monkeypatch.setenv("MIRIS_UPLOAD_GATE_FUNCTION_NAME", "gate-fn")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "asset-table")
    import vamsExecuteMirisUpload
    return importlib.reload(vamsExecuteMirisUpload)


def _resolved(aux=AUX):
    return {
        "assetId": "a1", "databaseId": "db1",
        "inputS3AssetFilePath": f"s3://b/{ROOT}scene.usda",
        "outputS3AssetFilesPath": f"s3://b/{ROOT}", "outputS3AssetPreviewPath": "",
        "outputS3AssetMetadataPath": "", "inputOutputS3AssetAuxiliaryFilesPath": aux,
        "inputMetadataS3Location": "", "inputConfigurationS3Location": "s3://b/cfg.json",
        "orchestrationEventPrefix": "", "manifestUsed": True, "inputVersionId": "",
        "inputFiles": [{"assetId": "a1", "bucket": "b", "assetRootS3Key": ROOT}],
    }


def _execute(handler, index_rows, page_size=100_000, aux=AUX):
    table = IndexTable(index_rows, page_size=page_size)
    table.get_item = MagicMock(return_value={"Item": {"assetName": "Pump", "isDistributable": True}})
    gate_reply = {"StatusCode": 200, "Payload": MagicMock(
        read=lambda: json.dumps({"gate": "proceed", "claimFingerprint": "f" * 64}).encode())}
    s3 = FakeS3()
    with patch.object(handler.manifestHelper, "resolve_pipeline_inputs", return_value=_resolved(aux)), \
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
    return response, gate_body, open_payload, sfn, s3


def test_vams_execute_writes_the_nested_locations_and_hands_on_only_their_uri(handler):
    import assetNesting

    response, gate_body, open_payload, _, s3 = _execute(handler, [_row(f"{ROOT}parts/")])

    assert response["statusCode"] == 200
    uri = NESTED
    assert gate_body["nestedLocationsS3Uri"] == uri and open_payload["nestedLocationsS3Uri"] == uri
    assert "nestedLocations" not in gate_body and "nestedLocations" not in open_payload
    assert assetNesting.read_nested_locations(s3, uri) == [f"{ROOT}parts/"]



def test_a_workflow_job_name_does_not_move_the_nested_locations_object(handler):
    import assetNesting

    custom = "s3://vams-aux-bucket/pipelines/miris-upload-nightly/exec-aaaa1111/"
    response, gate_body, open_payload, _, s3 = _execute(handler, [_row(f"{ROOT}parts/")], aux=custom)

    assert response["statusCode"] == 200
    assert gate_body["nestedLocationsS3Uri"] == NESTED == open_payload["nestedLocationsS3Uri"]
    assert assetNesting.read_nested_locations(s3, NESTED) == [f"{ROOT}parts/"]

def test_an_asset_with_no_nested_asset_hands_on_no_uri_and_writes_nothing(handler):
    _, gate_body, open_payload, _, s3 = _execute(handler, [])

    assert gate_body["nestedLocationsS3Uri"] == "" and open_payload["nestedLocationsS3Uri"] == ""
    assert s3.objects == {}


def test_many_nested_locations_keep_the_payloads_small(handler):
    rows = [_row(f"{ROOT}parts/subassembly-{i:05d}/", asset_id=f"n{i}") for i in range(5000)]

    _, gate_body, open_payload, _, _ = _execute(handler, rows)

    assert len(json.dumps(open_payload)) < 4096 and len(json.dumps(gate_body)) < 4096


def test_an_unfinished_index_walk_fails_the_run_before_the_gate_claims(handler, monkeypatch):
    import assetNesting

    monkeypatch.setattr(assetNesting, "MAX_NESTED_LOCATION_PAGES", 2)
    rows = [_row(f"{ROOT}p{i}/", asset_id=f"n{i}") for i in range(10)]

    response, gate_body, _, sfn, _ = _execute(handler, rows, page_size=3)

    assert response["statusCode"] == 500
    assert gate_body is None
    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert "nested" in cause and "Miris" in cause


# --- openPipeline and constructPipeline -------------------------------------------------------


def _construct(monkeypatch, **overrides):
    monkeypatch.setenv("MIRIS_API_BASE_URL", "https://app.miris.com")
    monkeypatch.setenv("MIRIS_API_KEY_SECRET_ARN",
                       "arn:aws:secretsmanager:us-west-2:123456789012:secret:miris-integration-key-AbCdEf")
    import constructPipeline
    construct = importlib.reload(constructPipeline)
    # Made-up names of the same length as a deployment's, so the size test measures the real bound.
    bucket = "vams-core-demo-us-east-1-stora-assetbucket0a1b2c3d-abcdefghij12"
    run = "pipelines/miris-upload/0123456789abcdef0123456789abcdef"
    event = {
        "jobName": "miris-upload-a1b2c3d4", "inputS3AssetFilePath": f"s3://{bucket}/{ROOT}scene.usda",
        "inputVersionId": "ExampleVersionId0123456789abcdef",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://vams-core-demo-us-east-1--assetauxiliarybucket0a1b-abcdefghij12/{run}/",
        "outputS3AssetFilesPath": f"s3://{bucket}/{run}/output/files/",
        "outputS3AssetMetadataPath": f"s3://{bucket}/{run}/output/metadata/",
        "inputConfigurationS3Location": f"s3://{bucket}/{run}/config.json",
        "assetId": "a1", "databaseId": "db1", "assetRootS3Path": f"s3://{bucket}/{ROOT}", "mirisAssetName": "Pump",
    }
    event.update(overrides)
    return construct.lambda_handler(event, None)["definition"][0]


@pytest.mark.parametrize("given, expected", [
    (NESTED, {"bucketName": "vams-aux-bucket", "objectKey": NESTED_KEY}),
    ("", {}),
    (None, {}),
], ids=["object", "none", "absent"])
def test_construct_pipeline_puts_the_nested_locations_object_on_the_container_definition(monkeypatch, given, expected):
    overrides = {} if given is None else {"nestedLocationsS3Uri": given}

    stage = json.loads(_construct(monkeypatch, **overrides))["stages"][0]

    assert stage["nestedLocations"] == expected


# Amazon ECS allows 8,192 characters for all of a task's overrides, JSON included, and a Fargate job's
# command goes there. The definition carries a fixed-size object location, never the list.
ECS_TASK_OVERRIDES_LIMIT = 8192


def test_the_definition_stays_within_the_ecs_override_limit_whatever_is_nested(monkeypatch):
    with_object = _construct(monkeypatch, nestedLocationsS3Uri=(
        "s3://vams-core-demo-us-east-1--assetauxiliarybucket0a1b-abcdefghij12/pipelines/miris-upload/nested-locations/"
        "0123456789abcdef0123456789abcdef.json"))

    # The command is a JSON string inside the overrides' JSON, so every quote is escaped once more.
    assert len(json.dumps([with_object])) < ECS_TASK_OVERRIDES_LIMIT // 2
