# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recreating a database must drop the soft-deleted `<databaseId>#deleted` sibling.

`delete_database` keeps a tombstone under `<databaseId>#deleted`. `create_database`
used to write only the live row, so `list --show-deleted` kept reporting the old
record for the same id (#766).
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import createDatabase as handler
from models.databases import CreateDatabaseRequestModel


REQUEST = CreateDatabaseRequestModel(
    databaseId="factory-db",
    description="second life",
    defaultBucketId="550e8400-e29b-41d4-a716-446655440000",
)


@pytest.fixture
def tables(monkeypatch):
    database_table = MagicMock()
    buckets_table = MagicMock()
    buckets_table.query.return_value = {"Items": [{"bucketId": REQUEST.defaultBucketId}]}

    resource = MagicMock()

    def _table(name):
        if name == handler.db_database:
            return database_table
        if name == handler.s3_asset_buckets_table:
            return buckets_table
        raise AssertionError(f"unexpected table {name}")

    resource.Table.side_effect = _table
    monkeypatch.setattr(handler, "dynamodb", resource)
    return database_table


@pytest.mark.unit
class TestCreateDatabasePurgesDeletedSibling:
    def test_successful_create_deletes_the_soft_deleted_sibling(self, tables):
        response = handler.create_database(REQUEST)

        assert response.databaseId == REQUEST.databaseId
        tables.put_item.assert_called_once()
        tables.delete_item.assert_called_once_with(
            Key={"databaseId": f"{REQUEST.databaseId}#deleted"}
        )

    def test_conditional_failure_leaves_the_deleted_sibling_alone(self, tables):
        from botocore.exceptions import ClientError

        tables.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
            "PutItem",
        )

        with pytest.raises(handler.VAMSGeneralErrorResponse):
            handler.create_database(REQUEST)

        tables.delete_item.assert_not_called()
