# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Focused moto test for the tagsNamespacing migration step's copy logic.

Exercises copy_tags_to_global_partition directly (the importable copy worker the
run_tags_namespacing_step wrapper delegates to) against moto-backed legacy
single-key and V2 composite-key tables. Verifies GLOBAL vs existing-databaseId
placement and idempotency.

Run: backend/.venv/bin/python -m pytest test_tags_namespacing.py -q
"""

import importlib.util
import os

import boto3
import pytest
from moto import mock_aws

_HERE = os.path.dirname(os.path.abspath(__file__))
_REGION = "us-east-1"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v25_to_v26_migration", os.path.join(_HERE, "v2.5_to_v2.6_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _create_legacy_table(client, name, name_attr):
    """Legacy single-key table: PK = name attribute."""
    client.create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": name_attr, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": name_attr, "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_v2_table(client, name, name_attr):
    """V2 composite-key table: PK = databaseId, SK = name attribute."""
    client.create_table(
        TableName=name,
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": name_attr, "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": name_attr, "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@mock_aws
def test_copy_tags_to_global_partition_scopes_and_is_idempotent():
    client = boto3.client("dynamodb", region_name=_REGION)

    tag_legacy = "legacy-TagStorage"
    tag_v2 = "TagStorageTableV2"
    tagtype_legacy = "legacy-TagTypeStorage"
    tagtype_v2 = "TagTypeStorageTableV2"

    _create_legacy_table(client, tag_legacy, "tagName")
    _create_v2_table(client, tag_v2, "tagName")
    _create_legacy_table(client, tagtype_legacy, "tagTypeName")
    _create_v2_table(client, tagtype_v2, "tagTypeName")

    # Seed legacy tags: one bare-name (no databaseId -> GLOBAL), one already scoped to db-a.
    client.put_item(
        TableName=tag_legacy,
        Item={"tagName": {"S": "bare-tag"}, "tagTypeName": {"S": "color"}},
    )
    client.put_item(
        TableName=tag_legacy,
        Item={"tagName": {"S": "scoped-tag"}, "databaseId": {"S": "db-a"}},
    )
    # Seed one legacy tag type (bare-name -> GLOBAL).
    client.put_item(
        TableName=tagtype_legacy,
        Item={"tagTypeName": {"S": "color"}, "required": {"BOOL": False}},
    )

    cfg = {
        "tag_legacy_table_name": tag_legacy,
        "tag_table_name_v2": tag_v2,
        "tag_type_legacy_table_name": tagtype_legacy,
        "tag_type_table_name_v2": tagtype_v2,
    }

    counts = mig.copy_tags_to_global_partition(client, cfg, dry_run=False, limit=None)
    assert counts == {"copied": 3, "skipped": 0, "errors": 0}

    # bare-tag lands under GLOBAL, preserving its other attributes.
    global_bare = client.get_item(
        TableName=tag_v2, Key={"databaseId": {"S": "GLOBAL"}, "tagName": {"S": "bare-tag"}}
    )
    assert global_bare["Item"]["tagTypeName"]["S"] == "color"

    # scoped-tag lands under db-a, not GLOBAL.
    scoped = client.get_item(
        TableName=tag_v2, Key={"databaseId": {"S": "db-a"}, "tagName": {"S": "scoped-tag"}}
    )
    assert "Item" in scoped
    assert (
        "Item"
        not in client.get_item(
            TableName=tag_v2,
            Key={"databaseId": {"S": "GLOBAL"}, "tagName": {"S": "scoped-tag"}},
        )
    )

    # Tag type lands under GLOBAL.
    gtype = client.get_item(
        TableName=tagtype_v2,
        Key={"databaseId": {"S": "GLOBAL"}, "tagTypeName": {"S": "color"}},
    )
    assert "Item" in gtype

    # Second run copies nothing (idempotent) — the ConditionExpression skips existing rows.
    counts2 = mig.copy_tags_to_global_partition(client, cfg, dry_run=False, limit=None)
    assert counts2 == {"copied": 0, "skipped": 3, "errors": 0}


@mock_aws
def test_copy_tags_dry_run_writes_nothing():
    client = boto3.client("dynamodb", region_name=_REGION)

    tag_legacy = "legacy-TagStorage"
    tag_v2 = "TagStorageTableV2"
    _create_legacy_table(client, tag_legacy, "tagName")
    _create_v2_table(client, tag_v2, "tagName")
    client.put_item(TableName=tag_legacy, Item={"tagName": {"S": "bare-tag"}})

    cfg = {"tag_legacy_table_name": tag_legacy, "tag_table_name_v2": tag_v2}

    counts = mig.copy_tags_to_global_partition(client, cfg, dry_run=True, limit=None)
    assert counts["copied"] == 1 and counts["errors"] == 0
    # Nothing was actually written to the V2 table.
    assert client.scan(TableName=tag_v2)["Count"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
