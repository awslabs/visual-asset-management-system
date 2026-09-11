# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for asset tag validation scoped to the asset's own database + GLOBAL.

An asset belongs to exactly one database. Its stored tag names must resolve
within ``{asset.databaseId} ∪ {GLOBAL}`` — an asset in database A may carry A's
tags or GLOBAL tags, but NOT another database's tags.
"""

from unittest.mock import MagicMock

import pytest

import tests.handlers.assets.test_createAsset_conditional_put as _cap


def _load():
    """Load a FRESH createAsset module (bypassing the shared cache).

    Other asset tests replace ``validate_tags_exist`` / ``verify_all_required_tags_satisfied``
    on the shared cached module with MagicMocks; forcing a fresh load guarantees the
    real implementations under test here regardless of test execution order.
    """
    _cap._cached_module = None
    return _cap._load()


def _make_tag_table(ddb_resource):
    """Composite-key tag table: PK=databaseId (HASH), SK=tagName (RANGE)."""
    return ddb_resource.create_table(
        TableName="assetTagScope-tags",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "tagName", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "tagName", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "tagNameIndex",
                "KeySchema": [{"AttributeName": "tagName", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _make_tag_type_table(ddb_resource):
    """Composite-key tag-type table: PK=databaseId (HASH), SK=tagTypeName (RANGE)."""
    return ddb_resource.create_table(
        TableName="assetTagScope-tagTypes",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "tagTypeName", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "tagTypeName", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "tagTypeNameIndex",
                "KeySchema": [{"AttributeName": "tagTypeName", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _wire_create(m, tag_table, tag_type_table):
    """Point the module at the real (moto) tag tables; stub the rest of create_asset."""
    m.tag_table = tag_table
    m.tag_type_table = tag_type_table
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}  # no existing asset record
    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": "db-a"}}
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
    })
    m.check_s3_prefix_exists = MagicMock(return_value=False)
    m.create_prefix_folder = MagicMock()
    m.create_initial_version_record = MagicMock(return_value="v0")
    m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
    m.save_asset_details = MagicMock()
    m.update_asset_count = MagicMock()
    m.write_asset_history_record = MagicMock()


def _request_model(m, tags):
    return m.CreateAssetRequestModel(
        databaseId="db-a",
        assetId="asset-1",
        assetName="asset-1",
        description="tag scope test",
        isDistributable=True,
        tags=tags,
    )


@pytest.mark.unit
class TestAssetTagScope:
    def test_asset_accepts_own_db_and_global_tags(self, ddb_resource):
        m = _load()
        tag_table = _make_tag_table(ddb_resource)
        tag_type_table = _make_tag_type_table(ddb_resource)
        tag_table.put_item(Item={"databaseId": "db-a", "tagName": "priority",
                                 "tagTypeName": "Custom", "description": "d"})
        tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                 "tagTypeName": "System", "description": "d"})
        _wire_create(m, tag_table, tag_type_table)

        response = m.create_asset(
            _request_model(m, ["priority", "reviewed"]),
            {"tokens": ["user1"]},
        )
        assert response.assetId == "asset-1"
        m.save_asset_details.assert_called_once()

    def test_asset_rejects_other_db_tag(self, ddb_resource):
        m = _load()
        tag_table = _make_tag_table(ddb_resource)
        tag_type_table = _make_tag_type_table(ddb_resource)
        # Seed a tag only in another database's partition.
        tag_table.put_item(Item={"databaseId": "db-b", "tagName": "secret",
                                 "tagTypeName": "Custom", "description": "d"})
        _wire_create(m, tag_table, tag_type_table)

        with pytest.raises(ValueError):
            m.create_asset(
                _request_model(m, ["secret"]),
                {"tokens": ["user1"]},
            )
        m.save_asset_details.assert_not_called()

    def test_validate_tags_exist_scoped_directly(self, ddb_resource):
        m = _load()
        tag_table = _make_tag_table(ddb_resource)
        tag_table.put_item(Item={"databaseId": "db-a", "tagName": "priority",
                                 "tagTypeName": "Custom", "description": "d"})
        tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                 "tagTypeName": "System", "description": "d"})
        tag_table.put_item(Item={"databaseId": "db-b", "tagName": "secret",
                                 "tagTypeName": "Custom", "description": "d"})
        m.tag_table = tag_table

        assert m.validate_tags_exist(["priority", "reviewed"], "db-a") is True
        with pytest.raises(ValueError):
            m.validate_tags_exist(["secret"], "db-a")

    def test_required_tag_type_scoped_to_db(self, ddb_resource):
        """A required tag type in dbB does not constrain an asset created in dbA."""
        m = _load()
        tag_table = _make_tag_table(ddb_resource)
        tag_type_table = _make_tag_type_table(ddb_resource)
        # Required tag type + tag exist only in dbB; dbA is unaffected.
        tag_type_table.put_item(Item={"databaseId": "db-b", "tagTypeName": "Clearance",
                                      "required": "True", "description": "d"})
        tag_table.put_item(Item={"databaseId": "db-b", "tagName": "secret",
                                 "tagTypeName": "Clearance", "description": "d"})
        m.tag_table = tag_table
        m.tag_type_table = tag_type_table

        # No required tag types resolve within {dbA, GLOBAL}, so an empty tag set passes.
        assert m.verify_all_required_tags_satisfied([], "db-a") is True
