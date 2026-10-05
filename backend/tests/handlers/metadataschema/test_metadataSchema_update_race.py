# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A metadata schema update writes only what it changes, and only to a schema that still exists.

`update_metadata_schema` reads the schema, authorizes against the stored row, and then writes. A
whole-item write of the row it read undoes whatever happened in between: a schema deleted after the
read is recreated, and a concurrent update of other fields is reverted. The write is therefore a
targeted `update_item` conditioned on the schema existing, and a failed condition answers 404. Each
case runs against a moto table, so the condition and the REMOVE clause are evaluated by DynamoDB
semantics rather than by a stub that reports what it was handed.
"""

import json
import os

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

SCHEMA_ID = "schema-abc-123"
DATABASE_ID = "factory-db"
ENTITY_TYPE = svc.MetadataSchemaEntityType.FILE_METADATA.value
SORT_KEY = f"{DATABASE_ID}:{ENTITY_TYPE}"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}

_FIELDS = {"fields": [{"metadataFieldKeyName": "partNumber", "metadataFieldValueType": "string",
                       "required": False}]}


def _stored_schema(**overrides):
    row = {
        "metadataSchemaId": SCHEMA_ID,
        "databaseId:metadataEntityType": SORT_KEY,
        "databaseId": DATABASE_ID,
        "metadataSchemaEntityType": ENTITY_TYPE,
        "schemaName": "Test Schema",
        "fields": json.dumps(_FIELDS),
        "enabled": True,
        "fileKeyTypeRestriction": ".e57,.las",
    }
    row.update(overrides)
    return row


class _AllowAll:
    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        return True


@pytest.fixture
def table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        created = dynamodb.create_table(
            TableName="test-metadata-schema-table",
            KeySchema=[
                {"AttributeName": "metadataSchemaId", "KeyType": "HASH"},
                {"AttributeName": "databaseId:metadataEntityType", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metadataSchemaId", "AttributeType": "S"},
                {"AttributeName": "databaseId:metadataEntityType", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        created.put_item(Item=_stored_schema())
        with patch.object(svc, "metadata_schema_table", created), \
                patch.object(svc, "CasbinEnforcer", _AllowAll):
            yield created


def _row(table):
    return table.get_item(Key={"metadataSchemaId": SCHEMA_ID,
                               "databaseId:metadataEntityType": SORT_KEY}).get("Item")


def _read_then(table, between):
    """A schema lookup that returns the row it read, then lets `between` change the table."""
    def _lookup(metadata_schema_id):
        row = _row(table)
        between()
        return row
    return _lookup


@pytest.mark.unit
class TestUpdateDoesNotUndoAConcurrentWrite:
    def test_a_schema_deleted_after_the_read_is_not_recreated(self, table, monkeypatch):
        monkeypatch.setattr(svc, "get_metadata_schema_details", _read_then(
            table, lambda: table.delete_item(Key={"metadataSchemaId": SCHEMA_ID,
                                                  "databaseId:metadataEntityType": SORT_KEY})))

        with pytest.raises(svc.VAMSGeneralErrorResponse) as raised:
            svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, AUTHENTICATED)

        assert raised.value.status_code == 404
        assert "Metadata schema not found" in str(raised.value)
        assert table.scan()["Items"] == []

    def test_a_concurrent_update_of_another_field_is_kept(self, table, monkeypatch):
        monkeypatch.setattr(svc, "get_metadata_schema_details", _read_then(
            table, lambda: table.update_item(
                Key={"metadataSchemaId": SCHEMA_ID, "databaseId:metadataEntityType": SORT_KEY},
                UpdateExpression="SET schemaName = :n",
                ExpressionAttributeValues={":n": "Renamed by another editor"})))

        result = svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, AUTHENTICATED)

        assert result.success is True
        row = _row(table)
        assert row["schemaName"] == "Renamed by another editor"
        assert row["enabled"] is False
        assert row["modifiedBy"] == "some-user"
        assert json.loads(row["fields"]) == _FIELDS

    def test_an_uncontended_update_writes_its_fields(self, table):
        """Paired control: the stored schema is found and updated through the same write."""
        replacement = {"fields": [{"metadataFieldKeyName": "serialNumber",
                                   "metadataFieldValueType": "string", "required": True}]}

        svc.update_metadata_schema(
            SCHEMA_ID, {"schemaName": "Renamed", "fields": replacement}, AUTHENTICATED)

        row = _row(table)
        assert row["schemaName"] == "Renamed"
        assert json.loads(row["fields"]) == replacement
        assert row["enabled"] is True
        assert row["fileKeyTypeRestriction"] == ".e57,.las"
        assert len(table.scan()["Items"]) == 1

    @pytest.mark.parametrize("cleared", [None, "", "   "], ids=["null", "empty", "whitespace"])
    def test_clearing_the_restriction_removes_the_attribute(self, table, cleared):
        svc.update_metadata_schema(
            SCHEMA_ID, {"fileKeyTypeRestriction": cleared, "enabled": False}, AUTHENTICATED)

        row = _row(table)
        assert "fileKeyTypeRestriction" not in row
        assert row["enabled"] is False
        assert row["schemaName"] == "Test Schema"


@pytest.mark.unit
class TestOtherWriteFailuresKeepTheGenericError:
    def test_a_throttled_write_is_not_reported_as_not_found(self):
        """Negative control for the 404 mapping: only a failed existence condition is a miss."""
        table = MagicMock()
        table.query.return_value = {"Items": [_stored_schema()]}
        table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
            "UpdateItem")

        with patch.object(svc, "metadata_schema_table", table), \
                patch.object(svc, "CasbinEnforcer", _AllowAll):
            with pytest.raises(svc.VAMSGeneralErrorResponse) as raised:
                svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, AUTHENTICATED)

        assert raised.value.status_code == 400
        assert "Error updating metadata schema" in str(raised.value)
        table.put_item.assert_not_called()
