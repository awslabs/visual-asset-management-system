# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A retired bucket registration is kept for the assets that name it and offered for nothing new (#1889).

The deploy-time writer marks a registration that left the configuration `retired` rather than deleting
its row. No bucket sync and no grants serve it any more, so `GET /buckets` (the database picker) leaves
it out and a database cannot be created on it. The two Physna lookups by bucket name, which had no
caller, are gone (#1891).
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases import createDatabase as handler
from backend.backend.handlers.databases.databaseService import get_buckets
from models.databases import CreateDatabaseRequestModel

# A caller allowed to read every registration (Tier 2 `bucket` GET), so the listing shows the projection.
READER = {"tokens": ["reader"]}

_PHYSNA_COMMON = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "addon",
                              "physna", "physnaCommon.py")


def _scan_response(rows):
    def typed(value):
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}
    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


REQUEST = CreateDatabaseRequestModel(databaseId="factory-db", description="second life",
                                     defaultBucketId="550e8400-e29b-41d4-a716-446655440000")


def _create_with(monkeypatch, registration_rows):
    database_table = MagicMock()
    buckets_table = MagicMock()
    buckets_table.query.return_value = {"Items": registration_rows}
    resource = MagicMock()
    resource.Table.side_effect = lambda name: buckets_table if name == handler.s3_asset_buckets_table \
        else database_table
    monkeypatch.setattr(handler, "dynamodb", resource)
    return database_table


@pytest.mark.unit
class TestRetiredRegistration:
    def test_the_bucket_listing_leaves_a_retired_registration_out(self):
        rows = [
            {"bucketId": "aa11bb22-c092-475f-978a-d39e5d5a2657", "bucketName": "current", "baseAssetsPrefix": "/"},
            {"bucketId": "cc33dd44-c092-475f-978a-d39e5d5a2657", "bucketName": "gone", "baseAssetsPrefix": "/",
             "retired": True},
        ]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db, \
                patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer") as enforcer:
            enforcer.return_value.enforce.return_value = True
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, READER)
        assert [item.bucketName for item in result.Items] == ["current"]

    def test_a_database_cannot_be_created_on_a_retired_registration(self, monkeypatch):
        database_table = _create_with(monkeypatch, [{"bucketId": REQUEST.defaultBucketId, "retired": True}])
        with pytest.raises(Exception):
            handler.create_database(REQUEST)
        database_table.put_item.assert_not_called()

    def test_a_database_is_created_on_a_configured_registration(self, monkeypatch):
        database_table = _create_with(monkeypatch, [{"bucketId": REQUEST.defaultBucketId}])
        handler.create_database(REQUEST)
        database_table.put_item.assert_called_once()

    def test_the_unused_physna_lookups_by_bucket_name_are_gone(self):
        with open(os.path.abspath(_PHYSNA_COMMON), encoding="utf-8") as f:
            source = f.read()
        assert "def get_bucket_details_by_name(" not in source
        assert "def get_database_id_for_asset_id(" not in source
        assert json.dumps("bucketNameGSI") not in json.dumps(source)


# ------------------------------------------------------------------ PUT /database/{databaseId}

from backend.backend.handlers.databases import databaseService as svc  # noqa: E402
from backend.tests.handlers.databases.test_databaseService_targeted_update import (  # noqa: E402,F401 - fixtures
    AUTHENTICATED,
    DATABASE_ID,
    FakeDatabaseTable,
    _stored_database,
    allow_all,  # noqa: F401 - fixture
    wire,  # noqa: F401 - fixture
)


@pytest.mark.unit
class TestUpdateRefusesARetiredRegistration:
    def test_a_database_cannot_be_moved_onto_a_retired_registration(self, wire, allow_all):  # noqa: F811
        table = FakeDatabaseTable(_stored_database(defaultBucketId="bucket-1"))
        wire(table, bucket_items=[{"bucketId": "bucket-gone", "retired": True}])

        with pytest.raises(svc.VAMSGeneralErrorResponse) as raised:
            svc.update_database(DATABASE_ID, {"defaultBucketId": "bucket-gone"}, AUTHENTICATED)

        assert "Bucket ID not found" in str(raised.value)
        assert table.update_item_calls == []
        assert table.stored()["defaultBucketId"] == "bucket-1"

    def test_a_database_already_on_a_retired_registration_can_still_be_edited(self, wire, allow_all):  # noqa: F811
        """The edit form sends the current defaultBucketId with every update."""
        table = FakeDatabaseTable(_stored_database(defaultBucketId="bucket-gone"))
        wire(table, bucket_items=[{"bucketId": "bucket-gone", "retired": True}])

        svc.update_database(DATABASE_ID, {"description": "after", "defaultBucketId": "bucket-gone"},
                            AUTHENTICATED)

        assert table.stored()["description"] == "after"

    def test_a_database_can_be_moved_onto_a_configured_registration(self, wire, allow_all):  # noqa: F811
        table = FakeDatabaseTable(_stored_database(defaultBucketId="bucket-1"))
        wire(table, bucket_items=[{"bucketId": "bucket-2"}])

        svc.update_database(DATABASE_ID, {"defaultBucketId": "bucket-2"}, AUTHENTICATED)

        assert table.stored()["defaultBucketId"] == "bucket-2"
