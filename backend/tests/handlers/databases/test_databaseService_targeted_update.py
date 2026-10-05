# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database-record writes on the database edit path.

`update_database` reads the database record for its Casbin check and writes it after that check,
while the asset recount (`assetCount.update_asset_count`) writes the same record after every asset
create, archive, unarchive and permanent delete. A full-record write from the edit path reverts
whatever the recount committed in between, persists the `object__type` marker set for the Casbin
check, and recreates a database deleted in the meantime. The edit path must therefore write only the
fields the caller supplied, and only to a record that still exists.
"""

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

from backend.backend.handlers.databases import databaseService as svc

DATABASE_ID = "factory-db"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}


class FakeDatabaseTable:
    """In-memory database table that applies a targeted SET update to the stored item.

    Records put_item separately so a full-record write is distinguishable from an attribute update,
    and honors an attribute_exists ConditionExpression so a write against a removed record fails the
    way DynamoDB fails it. `after_read` runs once the handler's read has returned, which is where a
    concurrent writer lands between the read and the write.
    """

    def __init__(self, item=None, after_read=None):
        self.items = {}
        if item is not None:
            self.items[item["databaseId"]] = dict(item)
        self.after_read = after_read
        self.put_item_calls = []
        self.update_item_calls = []
        self.updated_attributes = []

    def stored(self):
        return self.items.get(DATABASE_ID)

    def get_item(self, Key, **kwargs):
        item = self.items.get(Key["databaseId"])
        response = {"Item": dict(item)} if item is not None else {}
        if self.after_read is not None:
            self.after_read(self)
        return response

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
        "assetCount": "5",
        "defaultBucketId": "bucket-1",
        "restrictMetadataOutsideSchemas": False,
        "restrictFileUploadsToExtensions": "",
    }
    item.update(overrides)
    return item


@pytest.fixture
def allow_all(monkeypatch):
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    monkeypatch.setattr(svc, "CasbinEnforcer", enforcer)
    return enforcer


@pytest.fixture
def wire(monkeypatch, real_to_update_expr):
    """Route `dynamodb.Table(name)` to the database fake and a bucket-table stub, with the shipped
    update-expression builder bound onto the handler."""
    def _wire(database_table, bucket_items=None):
        buckets_table = MagicMock()
        buckets_table.query.return_value = {"Items": bucket_items or []}
        tables = {svc.db_database: database_table, svc.s3_asset_buckets_table: buckets_table}
        resource = MagicMock()
        resource.Table.side_effect = lambda name: tables[name]
        monkeypatch.setattr(svc, "dynamodb", resource)
        monkeypatch.setattr(svc, "to_update_expr", real_to_update_expr, raising=False)
        # monkeypatch reaches the handler only when it reads these names from its own globals.
        assert svc.update_database.__globals__ is svc.__dict__
        return buckets_table
    return _wire


@pytest.mark.unit
class TestUpdateDatabaseTargetedWrite:
    def test_concurrent_recount_is_not_reverted(self, wire, allow_all):
        """A recount that lands between the edit's read and its write keeps its value."""
        def recount(table):
            table.items[DATABASE_ID]["assetCount"] = "6"

        table = FakeDatabaseTable(_stored_database(), after_read=recount)
        wire(table)

        svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)

        assert table.stored()["assetCount"] == "6"
        assert table.stored()["description"] == "after"

    def test_writes_only_the_supplied_fields(self, wire, allow_all):
        table = FakeDatabaseTable(_stored_database())
        wire(table, bucket_items=[{"bucketId": "bucket-2"}])

        svc.update_database(DATABASE_ID, {
            "description": "after",
            "defaultBucketId": "bucket-2",
            "restrictMetadataOutsideSchemas": None,
            "restrictFileUploadsToExtensions": ".glb,.usd",
        }, AUTHENTICATED)

        assert table.put_item_calls == []
        assert table.updated_attributes == [{
            "description": "after",
            "defaultBucketId": "bucket-2",
            "restrictFileUploadsToExtensions": ".glb,.usd",
        }]
        assert table.update_item_calls[0]["Key"] == {"databaseId": DATABASE_ID}
        assert "attribute_exists(databaseId)" in table.update_item_calls[0]["ConditionExpression"]
        # Fields the caller did not supply are left exactly as stored.
        assert table.stored()["restrictMetadataOutsideSchemas"] is False
        assert table.stored()["dateCreated"] == "2026-01-01T00:00:00"

    def test_false_and_empty_values_are_written(self, wire, allow_all):
        """Only None means "not supplied": False and "" are real values that clear a setting."""
        table = FakeDatabaseTable(_stored_database(
            restrictMetadataOutsideSchemas=True, restrictFileUploadsToExtensions=".glb"))
        wire(table)

        svc.update_database(DATABASE_ID, {
            "restrictMetadataOutsideSchemas": False,
            "restrictFileUploadsToExtensions": "",
        }, AUTHENTICATED)

        assert table.updated_attributes == [{
            "restrictMetadataOutsideSchemas": False,
            "restrictFileUploadsToExtensions": "",
        }]
        assert table.stored()["restrictMetadataOutsideSchemas"] is False
        assert table.stored()["restrictFileUploadsToExtensions"] == ""

    def test_casbin_type_marker_is_not_persisted(self, wire, allow_all):
        """`object__type` is added to the read copy for the Casbin check and must never be stored."""
        table = FakeDatabaseTable(_stored_database())
        wire(table)

        svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)

        assert "object__type" not in table.stored()
        assert allow_all.return_value.enforce.call_args[0][0]["object__type"] == "database"

    def test_database_deleted_mid_edit_is_not_recreated(self, wire, allow_all):
        """Delete moves the record to `<id>#deleted`; the edit must not bring the live key back."""
        def delete(table):
            table.items.pop(DATABASE_ID, None)

        table = FakeDatabaseTable(_stored_database(), after_read=delete)
        wire(table)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match="Database not found"):
            svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)

        assert table.put_item_calls == []
        assert DATABASE_ID not in table.items

    def test_no_settable_field_writes_nothing(self, wire, allow_all):
        """A direct caller that supplies only None values gets the model's rejection, not a write."""
        table = FakeDatabaseTable(_stored_database())
        wire(table)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match="At least one field"):
            svc.update_database(DATABASE_ID, {"description": None}, AUTHENTICATED)

        assert table.put_item_calls == []
        assert table.update_item_calls == []

    def test_other_client_errors_are_not_reported_as_not_found(self, monkeypatch, wire, allow_all):
        """Control: only the conditional-check failure means the database is gone."""
        table = FakeDatabaseTable(_stored_database())
        wire(table)

        def denied(**kwargs):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                              "UpdateItem")

        monkeypatch.setattr(table, "update_item", denied)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match="Error updating database"):
            svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)
