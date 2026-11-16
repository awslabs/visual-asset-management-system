# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A removed asset link updates Garnet: its link entity and both of its assets (#1532).

The asset links table is keyed on `assetLinkId`, so a REMOVE record's `Keys` do not name the two
assets; only its `OldImage` does. Reading them from `Keys` sent nothing for a removed link: the link
entity was never removed from Garnet and neither asset entity was refreshed.
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


def _run(module, record, missing_assets=()):
    """Run the link handler with every lookup stubbed; return the entity ids it sent."""
    sent = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            module, "send_to_garnet_ingestion_queue",
            side_effect=lambda entity: sent.append(entity["id"]) or True))
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

    def test_link_delete_removes_the_link_and_refreshes_both_assets(self, garnet_indexer):
        results, sent = _run(garnet_indexer, _remove_record("parent1", "child1"))

        assert sent == ["urn:vams:assetlink:l1", "urn:asset:parent1", "urn:asset:child1"]
        assert results == [True, True, True]

    def test_permanent_asset_delete_refreshes_the_surviving_asset(self, garnet_indexer):
        """A permanent asset delete removes each of its links; the deleted asset reads back
        absent and only the survivor is refreshed."""
        results, sent = _run(garnet_indexer, _remove_record("parent1", "deleted1"),
                             missing_assets={"deleted1"})

        assert sent == ["urn:vams:assetlink:l1", "urn:asset:parent1"]
        assert results == [True, True]

    def test_remove_without_old_image_is_skipped(self, garnet_indexer):
        """A record from a stream that carries the new image only names no assets: skipped as
        before, not failed."""
        results, sent = _run(garnet_indexer, _remove_record("parent1", "child1",
                                                            with_old_image=False))

        assert sent == []
        assert results == [True]
