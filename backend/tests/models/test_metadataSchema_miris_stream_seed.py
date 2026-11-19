# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The seeded GLOBAL "Miris stream" file metadata schema (the JSON the Miris add-on construct writes
with a custom-resource putItem) passes the request and response models, applies to USD files only,
and accepts the metadata the Miris upload pipeline writes."""

import datetime
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aws_lambda_powertools.utilities.parser import parse
from boto3.dynamodb.types import TypeSerializer

from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.common.metadataSchemaValidation import (
    get_aggregated_schemas,
    validate_metadata_against_schema,
)
from backend.tests.pagingStub import Pager

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPO_ROOT / "infra" / "lib" / "nestedStacks" / "addon" / "miris" / "constructs"
    / "dynamodb-metadataschema-miris-stream.json"
)
STREAM_METADATA_PATH = (
    REPO_ROOT / "backendPipelines" / "conversion" / "mirisUpload" / "container" / "stream_metadata.py"
)

_TABLE = "test-metadata-schema-table"
_CONTROL_FIELD = "everyFileField"

# The Miris metadata contract: key -> value type. Every key is optional.
CONTRACT = {
    "miris_asset_uuid": "string",
    "miris_source_version_id": "string",
    "miris_uploaded_at": "date",
    "miris_display_name": "string",
    "miris_dependency_manifest": "string",
}


@pytest.fixture(scope="module")
def definition():
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def stream_metadata():
    spec = importlib.util.spec_from_file_location("miris_stream_metadata", STREAM_METADATA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    msv._schema_cache.clear()
    yield
    msv._schema_cache.clear()


def _seeded_item(definition):
    """The schema row exactly as the CDK construct's putItem writes it, in typed DynamoDB form."""
    serializer = TypeSerializer()
    item = {
        "metadataSchemaId": definition["metadataSchemaId"],
        "databaseId:metadataEntityType":
            f"{definition['databaseId']}:{definition['metadataSchemaEntityType']}",
        "databaseId": definition["databaseId"],
        "metadataSchemaEntityType": definition["metadataSchemaEntityType"],
        "schemaName": definition["schemaName"],
        "fileKeyTypeRestriction": definition["fileKeyTypeRestriction"],
        "fields": json.dumps({"fields": definition["fields"]}),
        "enabled": definition["enabled"],
        "dateCreated": "2026-10-14T00:00:00.000Z",
        "dateModified": "2026-10-14T00:00:00.000Z",
        "createdBy": "SYSTEM_USER",
        "modifiedBy": "SYSTEM_USER",
    }
    return {key: serializer.serialize(value) for key, value in item.items()}


def _control_item():
    """An unrestricted GLOBAL schema served beside the seeded one, so an empty aggregate is a failure."""
    return {
        "metadataSchemaId": {"S": "control-schema"},
        "databaseId": {"S": "GLOBAL"},
        "schemaName": {"S": "control"},
        "enabled": {"BOOL": True},
        "fields": {"S": json.dumps({"fields": [
            {"metadataFieldKeyName": _CONTROL_FIELD, "metadataFieldValueType": "string"},
        ]})},
    }


def _aggregate(definition, file_path):
    msv._schema_cache.clear()
    pager = Pager({"Items": [_seeded_item(definition), _control_item()]}, name="schema query")
    client = MagicMock()
    client.query.side_effect = pager
    aggregated = get_aggregated_schemas(
        database_ids=["GLOBAL"],
        entity_type="fileMetadata",
        file_path=file_path,
        dynamodb_client=client,
        schema_table_name=_TABLE,
    )
    assert pager.calls, "nothing read the schema stub"
    assert pager.calls[0]["ExpressionAttributeValues"] == {":pkValue": {"S": "GLOBAL:fileMetadata"}}
    assert _CONTROL_FIELD in aggregated, f"the control schema is missing: {sorted(aggregated)}"
    return aggregated


@pytest.mark.unit
class TestTheSeededDefinition:

    def test_it_is_a_global_usd_file_schema_with_a_valid_fixed_id(self, definition):
        from common.validators import validate
        assert (definition["databaseId"], definition["metadataSchemaEntityType"], definition["schemaName"]) == (
            "GLOBAL", "fileMetadata", "Miris stream")
        assert definition["fileKeyTypeRestriction"] == ".usd,.usda,.usdc,.usdz"
        assert definition["enabled"] is True
        assert definition["metadataSchemaId"] == "miris-stream-file-schema"
        valid, message = validate({"metadataSchemaId": {"value": definition["metadataSchemaId"], "validator": "ID"}})
        assert valid, message

    def test_it_passes_the_create_request_model(self, definition):
        from models.metadataSchema import CreateMetadataSchemaRequestModel, MetadataSchemaEntityType
        request = parse(
            event={
                "databaseId": definition["databaseId"],
                "metadataSchemaEntityType": definition["metadataSchemaEntityType"],
                "schemaName": definition["schemaName"],
                "fileKeyTypeRestriction": definition["fileKeyTypeRestriction"],
                "fields": {"fields": definition["fields"]},
                "enabled": definition["enabled"],
            },
            model=CreateMetadataSchemaRequestModel,
        )
        assert request.metadataSchemaEntityType == MetadataSchemaEntityType.FILE_METADATA
        assert len(request.fields.fields) == len(CONTRACT)

    def test_each_field_passes_the_field_model_with_the_contract_type(self, definition):
        from models.metadataSchema import MetadataSchemaFieldModel
        fields = [MetadataSchemaFieldModel(**field) for field in definition["fields"]]
        assert len(fields) == 5
        assert [field["sequence"] for field in definition["fields"]] == list(range(5))
        assert {f.metadataFieldKeyName: f.metadataFieldValueType.value for f in fields} == CONTRACT
        assert [f.metadataFieldKeyName for f in fields] == list(CONTRACT)
        assert all(f.required is False for f in fields)
        assert all(f.defaultMetadataFieldValue is None for f in fields)
        # Stored types are the model's own enum values, not a form it normalizes.
        assert [f["metadataFieldValueType"] for f in definition["fields"]] == list(CONTRACT.values())


@pytest.mark.unit
class TestTheSeededItem:

    def test_it_reads_back_through_the_response_model(self, definition):
        from boto3.dynamodb.types import TypeDeserializer
        from models.metadataSchema import MetadataSchemaResponseModel
        deserializer = TypeDeserializer()
        item = {k: deserializer.deserialize(v) for k, v in _seeded_item(definition).items()}
        item["fields"] = json.loads(item["fields"])
        response = MetadataSchemaResponseModel(**item)
        assert response.metadataSchemaId == "miris-stream-file-schema"
        assert response.fileKeyTypeRestriction == ".usd,.usda,.usdc,.usdz"
        assert [f.metadataFieldKeyName for f in response.fields.fields] == list(CONTRACT)

    @pytest.mark.parametrize("file_path", ["scene/model.usd", "Model.USDZ"])
    def test_it_applies_to_usd_files(self, definition, file_path):
        aggregated = _aggregate(definition, file_path)
        for key, value_type in CONTRACT.items():
            assert aggregated[key]["metadataFieldValueType"] == value_type
            assert aggregated[key]["required"] is False

    @pytest.mark.parametrize("file_path", ["model.glb", "notes.txt"])
    def test_it_does_not_apply_to_other_files(self, definition, file_path):
        aggregated = _aggregate(definition, file_path)
        assert set(CONTRACT).isdisjoint(aggregated), sorted(aggregated)


@pytest.mark.unit
class TestThePipelineWriteValidates:

    def _metadata(self, stream_metadata, **overrides):
        args = {
            "asset_uuid": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "source_version_id": "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY",
            "display_name": "model.usd",
            "uploaded_at": datetime.datetime(2026, 10, 14, 9, 30, 15, tzinfo=datetime.timezone.utc),
            "dependency_manifest": "ab" * 32,
        }
        args.update(overrides)
        body = stream_metadata.build_stream_metadata_body(**args)
        return {
            entry["metadataKey"]: {
                "metadataValue": entry["metadataValue"],
                "metadataValueType": entry["metadataValueType"],
            }
            for entry in body["metadata"]
        }

    @pytest.mark.parametrize("source_version_id", ["3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY", ""])
    def test_the_pipeline_write_validates_against_the_schema(
        self, definition, stream_metadata, source_version_id
    ):
        aggregated = _aggregate(definition, "scene/model.usd")
        metadata = self._metadata(stream_metadata, source_version_id=source_version_id)
        valid, errors, _ = validate_metadata_against_schema(metadata, aggregated, "POST")
        assert valid, errors

    @pytest.mark.parametrize("key, value, value_type, accepted", [
        ("miris_asset_uuid", "0f8fad5b-d9cb-469f-a165-70867728950e", "string", True),
        ("miris_uploaded_at", "2026-10-14T09:30:15Z", "string", False),
    ], ids=["hand-edit-uuid-only", "mistyped-upload-time"])
    def test_a_hand_edit_is_checked_against_the_schema(self, definition, key, value, value_type, accepted):
        aggregated = _aggregate(definition, "scene/model.usd")
        metadata = {key: {"metadataValue": value, "metadataValueType": value_type}}
        valid, errors, _ = validate_metadata_against_schema(metadata, aggregated, "POST")
        assert valid is accepted
        assert accepted or any(key in e for e in errors), errors
