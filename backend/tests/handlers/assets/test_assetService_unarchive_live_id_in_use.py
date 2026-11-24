# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unarchiving is refused while the database holds a live asset with the same id (#1860).

An asset's comments, subscription, versions and metadata are keyed by (databaseId, assetId), so the
archived asset and a live asset of the same database with the same id would share them, and moving
the archived row back would replace the live asset's record. The unarchive refuses before any file
or record is touched, and the write itself is conditional on no live row, for a live asset created
between the check and the write.
"""

import pytest
from botocore.exceptions import ClientError

from tests.handlers.assets.test_assetService_unarchive_deleted_database import (
    _ASSET,
    _CLAIMS,
    _DB,
    _archived_asset,
    _request,
    svc,  # noqa: F401  (fixture)
)


def _rows(svc, live=None):
    rows = {(f"{_DB}#deleted", _ASSET): _archived_asset()}
    if live is not None:
        rows[(_DB, _ASSET)] = live
    svc.asset_table.get_item.side_effect = lambda Key, **kwargs: (
        {"Item": dict(rows[(Key["databaseId"], Key["assetId"])])}
        if (Key["databaseId"], Key["assetId"]) in rows else {})
    svc.db_table.get_item.return_value = {"Item": {"databaseId": _DB}}


@pytest.mark.unit
class TestALiveSameIdAssetBlocksTheUnarchive:
    def test_the_unarchive_is_refused_and_nothing_moves(self, svc):
        _rows(svc, live={"databaseId": _DB, "assetId": _ASSET, "assetName": "live one"})

        with pytest.raises(svc.VAMSGeneralErrorResponse, match="already holds a live asset"):
            svc.unarchive_asset(_DB, _ASSET, _request(svc, unarchiveFiles=True), dict(_CLAIMS))

        svc.unarchive_multi_assetFiles.assert_not_called()
        svc.asset_table.put_item.assert_not_called()
        svc.asset_table.delete_item.assert_not_called()

    def test_a_live_asset_created_meanwhile_fails_the_conditional_write(self, svc):
        _rows(svc)
        svc.asset_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")

        with pytest.raises(svc.VAMSGeneralErrorResponse, match="already holds a live asset"):
            svc.unarchive_asset(_DB, _ASSET, _request(svc), dict(_CLAIMS))

        # The write carried a condition (the stub refused it as DynamoDB would), and the archived row stays
        assert "ConditionExpression" in svc.asset_table.put_item.call_args.kwargs
        svc.asset_table.delete_item.assert_not_called()

    def test_without_a_live_asset_the_unarchive_proceeds(self, svc):
        _rows(svc)

        result = svc.unarchive_asset(_DB, _ASSET, _request(svc), dict(_CLAIMS))

        assert result.success is True
        assert svc.asset_table.put_item.call_args.kwargs["Item"]["databaseId"] == _DB
        svc.asset_table.delete_item.assert_called_once_with(Key={"databaseId": f"{_DB}#deleted", "assetId": _ASSET})
