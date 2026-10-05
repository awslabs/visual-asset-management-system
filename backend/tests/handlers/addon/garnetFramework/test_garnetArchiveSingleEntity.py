# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archiving keeps one Garnet entity per asset and per database, and flips its isArchived flag.

Archiving an asset writes its record under the `{databaseId}#deleted` partition key and then deletes
the live record (`assetService.archive_asset`); unarchiving reverses the two writes. Deleting a
database rewrites the database record the same way (`databaseService.delete_database`). The indexers
built the entity id from the stored database id, so the INSERT under the archived key wrote a second
entity, `urn:vams:asset:db1#deleted:a1`, whose id carries `#` (a URI fragment delimiter), and only
that second entity was marked `isArchived: true`. The REMOVE of the live record sends a minimal
`{id, type}` entity, which the ingestion queue upserts with `options=update`, so the original entity
kept `isArchived: false` and `VAMSFile.belongsToAsset` kept pointing at it.

The entity id, scope and `databaseId` name the live database for an archived record. The asset-level
reads (metadata, current version, relationship flags, links) use the live database id too, because
archiving moves only the asset record; read under `db1#deleted` they came back empty, and the
archived entity would have been sent `hasChildren: false`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.addon.garnetFramework import (
    garnetDataIndexAsset,
    garnetDataIndexDatabase,
    garnetDataIndexFile,
)

LIVE_ASSET = {"databaseId": "db1", "assetId": "a1", "assetName": "Pump"}
ARCHIVED_ASSET = dict(LIVE_ASSET, databaseId="db1#deleted", status="archived")
ASSET_ENTITY_ID = "urn:vams:asset:db1:a1"
DATABASE_ENTITY_ID = "urn:vams:database:db1"


def _insert(**keys):
    return {"eventName": "INSERT",
            "dynamodb": {"NewImage": {name: {"S": value} for name, value in keys.items()}}}


def _remove(**keys):
    return {"eventName": "REMOVE",
            "dynamodb": {"Keys": {name: {"S": value} for name, value in keys.items()}}}


def _key_value(kwargs):
    """The value a boto3 KeyConditionExpression compares its key against."""
    return kwargs["KeyConditionExpression"].get_expression()["values"][1]


def _table(rows_by_key):
    """A table whose query answers from rows keyed by the KeyConditionExpression value."""
    def query(**kwargs):
        return {"Items": list(rows_by_key.get(_key_value(kwargs), []))}
    return MagicMock(query=MagicMock(side_effect=query))


def _links_table():
    """One parentChild link from the asset, stored under the live database id."""
    def query(**kwargs):
        if kwargs.get("IndexName") == "fromAssetGSI" and _key_value(kwargs) == "db1:a1":
            relationship = kwargs.get("FilterExpression")
            if relationship is None or \
                    relationship.get_expression()["values"][1] == "parentChild":
                return {"Items": [{"assetLinkId": "link-1",
                                   "relationshipType": "parentChild"}]}
        return {"Items": []}
    return MagicMock(query=MagicMock(side_effect=query))


def _run_asset_stream(records, rows):
    """Run handle_asset_stream over the records, reading asset rows keyed by stored database id."""
    m = garnetDataIndexAsset
    metadata = _table({"db1:a1:/": [{"metadataKey": "site", "metadataValue": "plant-a",
                                     "metadataValueType": "string"}]})
    versions = _table({"db1:a1": [{"assetVersionId": "3", "isCurrentVersion": True}]})
    with patch.object(m, "get_asset_details",
                      side_effect=lambda database_id, asset_id: rows.get(database_id)), \
            patch.object(m, "get_bucket_details", return_value=None), \
            patch.object(m, "asset_file_metadata_table", metadata), \
            patch.object(m, "asset_versions_table", versions), \
            patch.object(m, "asset_links_table", _links_table()), \
            patch.object(m, "get_asset_link_details", return_value=None) as link_details, \
            patch.object(m, "send_to_garnet_ingestion_queue", return_value=True) as send, \
            patch.object(m, "_record_sync") as record_sync:
        results = [m.handle_asset_stream(record) for record in records]
    return SimpleNamespace(
        results=results,
        sent=[call.args[0] for call in send.call_args_list],
        recorded_entity_ids=[call.kwargs["entity_id"] for call in record_sync.call_args_list],
        link_lookups=[call.args[0] for call in link_details.call_args_list],
    )


@pytest.mark.unit
class TestAssetArchiveKeepsOneEntity:
    def test_archived_record_names_the_live_asset_entity(self):
        entity = garnetDataIndexAsset.convert_asset_to_ngsi_ld(ARCHIVED_ASSET)
        assert entity["id"] == ASSET_ENTITY_ID, "the archived record built a second entity"
        assert entity["scope"] == ["/Database/db1/Asset/a1"]
        assert entity["databaseId"] == {"type": "Property", "value": "db1"}
        assert entity["isArchived"] == {"type": "Property", "value": True}
        assert entity["belongsToDatabase"]["object"] == DATABASE_ENTITY_ID

    def test_live_record_names_the_same_entity_unarchived(self):
        """Control for the test above."""
        entity = garnetDataIndexAsset.convert_asset_to_ngsi_ld(LIVE_ASSET)
        assert entity["id"] == ASSET_ENTITY_ID
        assert entity["isArchived"] == {"type": "Property", "value": False}

    def test_archive_marks_the_original_entity_archived(self):
        """Archive writes the archived record, then deletes the live one."""
        run = _run_asset_stream(
            [_insert(databaseId="db1#deleted", assetId="a1"),
             _remove(databaseId="db1", assetId="a1")],
            rows={"db1#deleted": ARCHIVED_ASSET})
        assert run.results == [True, True]
        assert [entity["id"] for entity in run.sent] == [ASSET_ENTITY_ID, ASSET_ENTITY_ID]
        assert run.recorded_entity_ids == [ASSET_ENTITY_ID, ASSET_ENTITY_ID]
        archived, removal = run.sent
        assert archived["isArchived"]["value"] is True
        # The removal is a minimal upsert: it carries no isArchived that could reset the flag
        assert removal == {"id": ASSET_ENTITY_ID, "type": "VAMSAsset"}

    def test_archived_entity_keeps_the_assets_metadata_version_and_links(self):
        run = _run_asset_stream([_insert(databaseId="db1#deleted", assetId="a1")],
                                rows={"db1#deleted": ARCHIVED_ASSET})
        archived = run.sent[0]
        assert archived["hasChildren"]["value"] is True, \
            "relationship flags were read under the archived database id"
        assert archived["metadata_site"]["value"] == "plant-a"
        assert archived["currentVersionId"]["value"] == "3"
        assert run.link_lookups == ["link-1"]

    def test_unarchive_marks_the_same_entity_live(self):
        """Unarchive writes the live record, then deletes the archived one."""
        run = _run_asset_stream(
            [_insert(databaseId="db1", assetId="a1"),
             _remove(databaseId="db1#deleted", assetId="a1")],
            rows={"db1": LIVE_ASSET})
        assert run.results == [True, True]
        assert [entity["id"] for entity in run.sent] == [ASSET_ENTITY_ID, ASSET_ENTITY_ID]
        assert run.sent[0]["isArchived"]["value"] is False
        assert run.sent[1] == {"id": ASSET_ENTITY_ID, "type": "VAMSAsset"}

    def test_reindex_of_an_archived_asset_marks_the_original_entity_archived(self):
        """The reindex utility touches an asset-level metadata row under the stored database id,
        `db1#deleted:a1:/` for an archived asset; that event re-sends the asset's one entity."""
        m = garnetDataIndexAsset
        record = _insert(**{"databaseId:assetId:filePath": "db1#deleted:a1:/"})
        rows = {"db1#deleted": ARCHIVED_ASSET}
        with patch.object(m, "get_asset_details",
                          side_effect=lambda database_id, asset_id: rows.get(database_id)), \
                patch.object(m, "get_bucket_details", return_value=None), \
                patch.object(m, "get_asset_metadata", return_value={}), \
                patch.object(m, "get_asset_version_info", return_value={}), \
                patch.object(m, "get_asset_relationship_flags", return_value={}), \
                patch.object(m, "send_to_garnet_ingestion_queue", return_value=True) as send, \
                patch.object(m, "_record_sync"):
            assert m.handle_asset_metadata_stream(record) is True
        entity = send.call_args.args[0]
        assert entity["id"] == ASSET_ENTITY_ID
        assert entity["isArchived"]["value"] is True

    def test_file_relationship_names_the_archived_asset_entity(self):
        """A file of an archived asset keeps the live database id in its S3 metadata, so its
        belongsToAsset target has to be the entity that carries isArchived: true."""
        file_entity = garnetDataIndexFile.convert_file_to_ngsi_ld(
            "db1", "a1", "/part.stp", {"assetName": "Pump"},
            {"bucketName": "bucket", "baseAssetsPrefix": ""}, {}, {}, None, True)
        asset_entity = garnetDataIndexAsset.convert_asset_to_ngsi_ld(ARCHIVED_ASSET)
        assert file_entity["belongsToAsset"]["object"] == asset_entity["id"]


@pytest.mark.unit
class TestDatabaseDeleteKeepsOneEntity:
    def test_deleted_record_names_the_live_database_entity(self):
        entity = garnetDataIndexDatabase.convert_database_to_ngsi_ld({"databaseId": "db1#deleted"})
        assert entity["id"] == DATABASE_ENTITY_ID, "the deleted record built a second entity"
        assert entity["scope"] == ["/Database/db1"]
        assert entity["databaseId"] == {"type": "Property", "value": "db1"}
        assert entity["isArchived"] == {"type": "Property", "value": True}

    def test_live_record_names_the_same_entity_unarchived(self):
        """Control for the test above."""
        entity = garnetDataIndexDatabase.convert_database_to_ngsi_ld({"databaseId": "db1"})
        assert entity["id"] == DATABASE_ENTITY_ID
        assert entity["isArchived"] == {"type": "Property", "value": False}

    def test_delete_marks_the_original_entity_archived(self):
        """Database delete writes the record under the archived key, then deletes the live one.
        Database metadata stays under the live database id."""
        m = garnetDataIndexDatabase
        rows = {"db1#deleted": {"databaseId": "db1#deleted", "description": "Plant A"}}
        metadata = _table({"db1": [{"metadataKey": "site", "metadataValue": "plant-a",
                                    "metadataValueType": "string"}]})
        with patch.object(m, "get_database_details",
                          side_effect=lambda database_id: rows.get(database_id)), \
                patch.object(m, "get_bucket_details", return_value=None), \
                patch.object(m, "database_metadata_table", metadata), \
                patch.object(m, "send_to_garnet_ingestion_queue", return_value=True) as send, \
                patch.object(m, "_record_sync") as record_sync:
            results = [m.handle_database_stream(_insert(databaseId="db1#deleted")),
                       m.handle_database_stream(_remove(databaseId="db1"))]
        sent = [call.args[0] for call in send.call_args_list]
        assert results == [True, True]
        assert [entity["id"] for entity in sent] == [DATABASE_ENTITY_ID, DATABASE_ENTITY_ID]
        assert [call.kwargs["entity_id"] for call in record_sync.call_args_list] == \
            [DATABASE_ENTITY_ID, DATABASE_ENTITY_ID]
        deleted, removal = sent
        assert deleted["isArchived"]["value"] is True
        assert deleted["metadata_site"]["value"] == "plant-a"
        assert removal == {"id": DATABASE_ENTITY_ID, "type": "VAMSDatabase"}
