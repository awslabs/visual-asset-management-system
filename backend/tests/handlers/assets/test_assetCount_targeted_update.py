# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database-record writes on the asset recount path.

`update_asset_count` runs after every asset create, archive, unarchive and permanent delete, and
from bucket sync. It counts the database's live assets and stores the count on the database record,
which the database edit API (`databaseService.update_database`) writes too. The recount owns exactly
one attribute, `assetCount`, so it must write only that, and only to a record that still exists: a
full-record write reverts a concurrent edit of any other field, and an unconditional write recreates
a database deleted after its last asset was archived.
"""

import boto3
import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.backend.handlers.assets import assetCount as count_module

DATABASE_ID = "db-1"
DATABASE_TABLE = "test-database-table"
ASSET_TABLE = "test-asset-table"


class FakeDatabaseTable:
    """In-memory database table that applies a targeted SET update to the stored item.

    Records put_item and query separately so a full-record read-modify-write is distinguishable
    from an attribute update, and honors an attribute_exists ConditionExpression so a write against
    a removed record fails the way DynamoDB fails it.
    """

    def __init__(self, item=None):
        self.items = {}
        if item is not None:
            self.items[item["databaseId"]] = dict(item)
        self.put_item_calls = []
        self.update_item_calls = []
        self.updated_attributes = []

    def stored(self):
        return self.items.get(DATABASE_ID)

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items.values()]}

    def put_item(self, Item, **kwargs):
        self.put_item_calls.append(Item)
        self.items[Item["databaseId"]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None, **kwargs):
        self.update_item_calls.append({"Key": Key, "ConditionExpression": ConditionExpression})
        item = self.items.get(Key["databaseId"])
        if ConditionExpression and "attribute_exists" in ConditionExpression and item is None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException",
                           "Message": "The conditional request failed"}},
                "UpdateItem")
        assert UpdateExpression.startswith("SET "), UpdateExpression
        written = {}
        for assignment in UpdateExpression[len("SET "):].split(", "):
            name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
            written[ExpressionAttributeNames[name_ref]] = ExpressionAttributeValues[value_ref]
        self.updated_attributes.append(written)
        if item is not None:
            item.update(written)


def _stored_database(**overrides):
    item = {
        "databaseId": DATABASE_ID,
        "description": "before",
        "dateCreated": "2026-01-01T00:00:00",
        "assetCount": "2",
        "defaultBucketId": "bucket-1",
        "restrictMetadataOutsideSchemas": False,
    }
    item.update(overrides)
    return item


def _wire(monkeypatch, database_table, count, during_count=None):
    """Route the module's resource and client to the fakes.

    The asset count comes back from the query paginator's `build_full_result`; `during_count` runs
    there, which is where a concurrent writer lands between any read of the database record and the
    recount's write.
    """
    resource = MagicMock()
    resource.Table.side_effect = lambda name: {DATABASE_TABLE: database_table}[name]

    def build_full_result():
        if during_count is not None:
            during_count(database_table)
        return {"Count": count, "Items": []}

    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value.build_full_result.side_effect = (
        build_full_result)
    monkeypatch.setattr(count_module, "dynamodb", resource)
    monkeypatch.setattr(count_module, "dynamodb_client", client)
    # monkeypatch reaches the function only when it reads these names from its own globals.
    assert count_module.update_asset_count.__globals__ is count_module.__dict__
    return client


@pytest.mark.unit
class TestUpdateAssetCountTargetedWrite:
    def test_concurrent_edit_is_not_reverted(self, monkeypatch):
        """A database edit that lands while the recount is counting keeps its value."""
        def edit(table):
            table.items[DATABASE_ID]["description"] = "edited concurrently"
            table.items[DATABASE_ID]["restrictMetadataOutsideSchemas"] = True

        table = FakeDatabaseTable(_stored_database())
        _wire(monkeypatch, table, count=3, during_count=edit)

        count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

        assert table.stored()["description"] == "edited concurrently"
        assert table.stored()["restrictMetadataOutsideSchemas"] is True
        assert table.stored()["assetCount"] == "3"

    def test_writes_only_asset_count_as_a_string(self, monkeypatch):
        """The count keeps its stored type: `get_database` reads it back through int()."""
        table = FakeDatabaseTable(_stored_database())
        _wire(monkeypatch, table, count=7)

        count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

        assert table.put_item_calls == []
        assert table.updated_attributes == [{"assetCount": "7"}]
        assert table.update_item_calls[0]["Key"] == {"databaseId": DATABASE_ID}
        assert "attribute_exists(databaseId)" in table.update_item_calls[0]["ConditionExpression"]

    def test_counts_the_requested_database_in_the_asset_table(self, monkeypatch):
        table = FakeDatabaseTable(_stored_database())
        client = _wire(monkeypatch, table, count=1)

        count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

        client.get_paginator.assert_called_with("query")
        paginate_kwargs = client.get_paginator.return_value.paginate.call_args.kwargs
        assert paginate_kwargs["TableName"] == ASSET_TABLE
        assert paginate_kwargs["KeyConditions"]["databaseId"]["AttributeValueList"] == [
            {"S": DATABASE_ID}]

    def test_deleted_database_is_not_recreated(self, monkeypatch):
        """Delete moves the record to `<id>#deleted`; a recount afterwards is a no-op, not a
        two-attribute live record and not an error for the asset operation that triggered it."""
        table = FakeDatabaseTable()
        _wire(monkeypatch, table, count=0)

        count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

        assert table.items == {}
        assert table.put_item_calls == []
        assert len(table.update_item_calls) == 1

    def test_other_client_errors_propagate(self, monkeypatch):
        """Control: only the conditional-check failure is treated as "database gone"."""
        table = FakeDatabaseTable(_stored_database())
        _wire(monkeypatch, table, count=1)

        def denied(**kwargs):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                              "UpdateItem")

        monkeypatch.setattr(table, "update_item", denied)

        with pytest.raises(ClientError) as excinfo:
            count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)
        assert excinfo.value.response["Error"]["Code"] == "AccessDeniedException"


def _moto_tables():
    """Moto-backed database and asset tables (PK databaseId; the asset table adds SK assetId)."""
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    database_table = resource.create_table(
        TableName=DATABASE_TABLE,
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    asset_table = resource.create_table(
        TableName=ASSET_TABLE,
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                   {"AttributeName": "assetId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                              {"AttributeName": "assetId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return resource, database_table, asset_table


@pytest.mark.unit
class TestUpdateAssetCountAgainstDynamoDB:
    """Controls against moto, so the update and condition expressions are parsed and applied by a
    DynamoDB implementation rather than by the fake above, which checks for the error code it raises
    itself."""

    def test_existing_record_keeps_its_other_attributes(self, monkeypatch):
        with mock_aws():
            resource, database_table, asset_table = _moto_tables()
            database_table.put_item(Item=_stored_database(assetCount="0"))
            asset_table.put_item(Item={"databaseId": DATABASE_ID, "assetId": "a1"})
            asset_table.put_item(Item={"databaseId": DATABASE_ID, "assetId": "a2"})
            asset_table.put_item(Item={"databaseId": "db-2", "assetId": "a3"})
            monkeypatch.setattr(count_module, "dynamodb", resource)
            monkeypatch.setattr(count_module, "dynamodb_client",
                                boto3.client("dynamodb", region_name="us-east-1"))

            count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

            stored = database_table.get_item(Key={"databaseId": DATABASE_ID})["Item"]
            assert stored["assetCount"] == "2"
            assert stored["description"] == "before"
            assert stored["dateCreated"] == "2026-01-01T00:00:00"
            assert stored["defaultBucketId"] == "bucket-1"
            assert stored["restrictMetadataOutsideSchemas"] is False

    def test_missing_record_stays_absent(self, monkeypatch):
        """The archived-asset delete after its database was deleted: only the tombstone exists."""
        with mock_aws():
            resource, database_table, asset_table = _moto_tables()
            tombstone = _stored_database(databaseId=f"{DATABASE_ID}#deleted")
            database_table.put_item(Item=tombstone)
            monkeypatch.setattr(count_module, "dynamodb", resource)
            monkeypatch.setattr(count_module, "dynamodb_client",
                                boto3.client("dynamodb", region_name="us-east-1"))

            count_module.update_asset_count(DATABASE_TABLE, ASSET_TABLE, {}, DATABASE_ID)

            assert "Item" not in database_table.get_item(Key={"databaseId": DATABASE_ID})
            assert database_table.get_item(
                Key={"databaseId": f"{DATABASE_ID}#deleted"})["Item"] == tombstone
