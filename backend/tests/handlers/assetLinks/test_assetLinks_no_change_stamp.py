# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creating, deleting or updating an asset link writes the link row and nothing on the asset table:
the compliance trigger records a link change on both linked assets from the link table's stream
(`common.assetProvenance.record_indexed_change`), so a link request costs exactly what it cost before
compliance. These tests fail on the request-path stamps they replace."""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import assetLinksService as service
from backend.backend.handlers.assetLinks import createAssetLink as cal
from models.assetLinks import CreateAssetLinkRequestModel

LINK_ID = "12345678-1234-1234-1234-123456789012"
LINK_ITEM = {
    "assetLinkId": LINK_ID,
    "fromAssetId": "assembly", "fromAssetDatabaseId": "db-1",
    "toAssetId": "part", "toAssetDatabaseId": "db-2",
    "relationshipType": "parentChild",
}
ENDPOINT_STAMPS = [("db-1", "assembly", "linkChange"), ("db-2", "part", "linkChange")]


def _stamped(mock):
    return [call.args[1:4] for call in mock.call_args_list]


def _create(links_table):
    request = CreateAssetLinkRequestModel(
        fromAssetId="assembly", fromAssetDatabaseId="db-1", toAssetId="part",
        toAssetDatabaseId="db-2", relationshipType="parentChild")
    with patch.object(cal, "validate_assets_exist", return_value=True), \
            patch.object(cal, "check_asset_permissions", return_value=True), \
            patch.object(cal, "check_existing_relationship", return_value=False), \
            patch.object(cal, "detect_cycle_in_parent_child", return_value=False), \
            patch.object(cal, "asset_links_table", links_table):
        return cal.create_asset_link(request, {"tokens": ["t"]})


@pytest.mark.unit
class TestCreatingALink:

    def test_the_link_is_written_and_the_asset_table_is_not_touched_after_it(self):
        links_table = MagicMock()
        asset_table = MagicMock(name="asset_storage_table")
        with patch.object(cal, "asset_storage_table", asset_table):
            _create(links_table)
        links_table.put_item.assert_called_once()
        assert asset_table.update_item.call_args_list == []
        assert asset_table.put_item.call_args_list == []


def _delete(links_table, metadata_table=None):
    metadata_table = metadata_table or MagicMock()
    metadata_table.query.return_value = {"Items": []}
    with patch.object(service, "asset_links_table", links_table), \
            patch.object(service, "asset_links_metadata_table", metadata_table), \
            patch.object(service, "get_asset_details", return_value={"assetId": "x"}), \
            patch.object(service, "check_asset_permission", return_value=True):
        return service.delete_asset_link(LINK_ID, {"tokens": ["t"]})


@pytest.mark.unit
class TestDeletingALink:

    def test_the_link_is_removed_and_the_asset_table_is_not_written(self):
        links_table = MagicMock()
        links_table.get_item.return_value = {"Item": dict(LINK_ITEM)}
        asset_table = MagicMock(name="asset_storage_table")
        with patch.object(service, "asset_storage_table", asset_table):
            _delete(links_table)
        links_table.delete_item.assert_called_once_with(Key={"assetLinkId": LINK_ID})
        assert asset_table.update_item.call_args_list == []


@pytest.mark.unit
def test_the_link_handlers_never_write_the_asset_table():
    """The handlers' asset-table use is read-only (the IAM grant carries no write action on it,
    infra handlerGrantScopingWave.test.ts), and no change-stamp helper is imported."""
    import inspect
    for module in (cal, service):
        source = inspect.getsource(module)
        for method in ("put_item", "update_item", "delete_item", "batch_writer"):
            assert f"asset_storage_table.{method}(" not in source
        assert "assetProvenance" not in source
