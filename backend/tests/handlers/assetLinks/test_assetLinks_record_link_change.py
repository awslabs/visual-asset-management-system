# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creating or deleting an asset link records `linkChange` on BOTH linked assets' rows, once the
link row is written or removed, so the compliance trigger re-evaluates the relationship and
links-scope metadata rules on each side. A write that fails stamps nothing, and an update of a
link's tags or alias -- which no compliance rule reads -- stamps nothing. The stamp itself is
`common.assetProvenance.stamp_asset_change` (its update shape is pinned in
tests/common/test_assetProvenance_stamp_asset_change.py); the directory conftest replaces it with a
mock per module."""

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

    def test_both_assets_are_stamped_after_the_link_is_written(self, stamp_asset_change):
        links_table = MagicMock()
        stamp = stamp_asset_change["createAssetLink"]
        order = []
        links_table.put_item.side_effect = lambda **kw: order.append("put")
        stamp.side_effect = lambda *a, **kw: order.append("stamp")
        _create(links_table)
        assert _stamped(stamp) == ENDPOINT_STAMPS
        assert all(call.args[0] is cal.asset_storage_table for call in stamp.call_args_list)
        assert order == ["put", "stamp", "stamp"]

    def test_a_link_that_was_not_written_stamps_nothing(self, stamp_asset_change):
        links_table = MagicMock()
        links_table.put_item.side_effect = RuntimeError("ProvisionedThroughputExceeded")
        with pytest.raises(RuntimeError):
            _create(links_table)
        stamp_asset_change["createAssetLink"].assert_not_called()


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

    def test_both_assets_are_stamped_after_the_link_is_removed(self, stamp_asset_change):
        links_table = MagicMock()
        links_table.get_item.return_value = {"Item": dict(LINK_ITEM)}
        stamp = stamp_asset_change["assetLinksService"]
        order = []
        links_table.delete_item.side_effect = lambda **kw: order.append("delete")
        stamp.side_effect = lambda *a, **kw: order.append("stamp")
        _delete(links_table)
        assert _stamped(stamp) == ENDPOINT_STAMPS
        assert all(call.args[0] is service.asset_storage_table for call in stamp.call_args_list)
        assert order == ["delete", "stamp", "stamp"]

    def test_a_link_that_was_not_removed_stamps_nothing(self, stamp_asset_change):
        links_table = MagicMock()
        links_table.get_item.return_value = {"Item": dict(LINK_ITEM)}
        links_table.delete_item.side_effect = RuntimeError("AccessDenied")
        with pytest.raises(RuntimeError):
            _delete(links_table)
        stamp_asset_change["assetLinksService"].assert_not_called()


@pytest.mark.unit
class TestUpdatingALink:

    def test_a_tag_or_alias_update_stamps_nothing(self, stamp_asset_change):
        links_table = MagicMock()
        links_table.get_item.return_value = {"Item": dict(LINK_ITEM)}
        links_table.query.return_value = {"Items": []}
        request = MagicMock(tags=["a"], assetLinkAliasId=None)
        with patch.object(service, "asset_links_table", links_table), \
                patch.object(service, "get_asset_details", return_value={"assetId": "x"}), \
                patch.object(service, "check_asset_permission", return_value=True):
            service.update_asset_link(LINK_ID, request, {"tokens": ["t"]})
        stamp_asset_change["assetLinksService"].assert_not_called()
        stamp_asset_change["createAssetLink"].assert_not_called()


@pytest.mark.unit
def test_the_link_handlers_write_the_asset_table_only_through_the_stamp():
    """The handlers' asset-table use stays read-only apart from the stamp: the IAM grant is
    `dynamodb:UpdateItem` alone (infra handlerGrantScopingWave.test.ts), so no other write may be
    added through the handle."""
    import inspect
    for module in (cal, service):
        source = inspect.getsource(module)
        for method in ("put_item", "update_item", "delete_item", "batch_writer"):
            assert f"asset_storage_table.{method}(" not in source
