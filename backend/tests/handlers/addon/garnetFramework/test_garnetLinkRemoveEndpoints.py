# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A removed asset link refreshes both of its assets in Garnet (#1532).

The asset links table is keyed on `assetLinkId`, so a REMOVE record's `Keys` do not name the two
assets; only its `OldImage` does. Reading them from `Keys` sent nothing for a removed link, so
neither asset entity's hasChildren, hasParents and hasRelated values were updated. Nothing is sent
for the link entity itself: the ingestion queue cannot remove an entity, and a removed link stays
in the knowledge graph, as the Garnet Framework limitations document.
"""

import importlib.util
import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

_GARNET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "garnetFramework",
)

DATABASE_ID = "garnet-db"


@pytest.fixture(scope="module")
def garnet_indexer():
    path = os.path.abspath(os.path.join(_GARNET_DIR, "garnetDataIndexAsset.py"))
    spec = importlib.util.spec_from_file_location("garnetDataIndexAsset_link_remove", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


def _remove_record(from_asset_id, to_asset_id, with_old_image=True):
    dynamodb_data = {"Keys": {"assetLinkId": {"S": "l1"}}}
    if with_old_image:
        dynamodb_data["OldImage"] = {
            "assetLinkId": {"S": "l1"},
            "fromAssetDatabaseId:fromAssetId": {"S": f"{DATABASE_ID}:{from_asset_id}"},
            "toAssetDatabaseId:toAssetId": {"S": f"{DATABASE_ID}:{to_asset_id}"},
            "relationshipType": {"S": "parentChild"},
        }
    return {"eventName": "REMOVE", "dynamodb": dynamodb_data}


def _insert_record(from_asset_id, to_asset_id):
    image = {
        "assetLinkId": {"S": "l1"},
        "fromAssetDatabaseId:fromAssetId": {"S": f"{DATABASE_ID}:{from_asset_id}"},
        "toAssetDatabaseId:toAssetId": {"S": f"{DATABASE_ID}:{to_asset_id}"},
        "relationshipType": {"S": "parentChild"},
    }
    return {"eventName": "INSERT", "dynamodb": {"Keys": {"assetLinkId": {"S": "l1"}}, "NewImage": image}}


def _run(module, record, missing_assets=()):
    """Run the link handler with every lookup stubbed; return the entity ids it sent."""
    sent = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            module, "send_to_garnet_ingestion_queue",
            side_effect=lambda entity: sent.append(entity["id"]) or True))
        stack.enter_context(patch.object(
            module, "get_asset_link_details", side_effect=lambda link_id: {"assetLinkId": link_id}))
        stack.enter_context(patch.object(module, "get_asset_link_metadata", return_value={}))
        stack.enter_context(patch.object(
            module, "convert_asset_link_to_ngsi_ld",
            side_effect=lambda details, *args: {"id": f"urn:vams:assetlink:{details['assetLinkId']}"}))
        stack.enter_context(patch.object(
            module, "get_asset_details",
            side_effect=lambda database_id, asset_id: None if asset_id in missing_assets
            else {"databaseId": database_id, "assetId": asset_id}))
        stack.enter_context(patch.object(module, "get_asset_metadata", return_value={}))
        stack.enter_context(patch.object(module, "get_asset_version_info", return_value={}))
        stack.enter_context(patch.object(
            module, "get_asset_relationship_flags",
            return_value={"has_children": False, "has_parents": False, "has_related": False}))
        stack.enter_context(patch.object(
            module, "convert_asset_to_ngsi_ld",
            side_effect=lambda details, *args: {"id": f"urn:asset:{details['assetId']}"}))
        results = module.handle_asset_links_stream(record)
    return results, sent


@pytest.mark.unit
class TestRemovedLinkReachesGarnet:

    def test_link_delete_refreshes_both_assets(self, garnet_indexer):
        results, sent = _run(garnet_indexer, _remove_record("parent1", "child1"))

        assert sent == ["urn:asset:parent1", "urn:asset:child1"]
        assert results == [True, True]

    def test_permanent_asset_delete_refreshes_the_surviving_asset(self, garnet_indexer):
        """A permanent asset delete removes each of its links; the deleted asset reads back
        absent and only the survivor is refreshed."""
        results, sent = _run(garnet_indexer, _remove_record("parent1", "deleted1"),
                             missing_assets={"deleted1"})

        assert sent == ["urn:asset:parent1"]
        assert results == [True]

    def test_remove_without_old_image_is_skipped(self, garnet_indexer):
        """A record from a stream that carries the new image only names no assets: skipped as
        before, not failed."""
        results, sent = _run(garnet_indexer, _remove_record("parent1", "child1",
                                                            with_old_image=False))

        assert sent == []
        assert results == [True]

    def test_link_create_still_sends_the_link_and_both_assets(self, garnet_indexer):
        """Positive control: only a removed link skips the link entity; a created link sends it
        before both assets."""
        results, sent = _run(garnet_indexer, _insert_record("parent1", "child1"))

        assert sent == ["urn:vams:assetlink:l1", "urn:asset:parent1", "urn:asset:child1"]
        assert results == [True, True, True]
