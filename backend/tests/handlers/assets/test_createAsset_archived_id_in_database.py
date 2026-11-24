# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A chosen asset id is unique within its database, archived assets included (#1860).

Comments, subscriptions, versions and metadata are keyed by (databaseId, assetId) and stay with an
archived asset, so a new live asset with an archived asset's id would inherit them. create_asset
refuses a chosen id the target database holds live or archived; the same id in another database is
still accepted, because assets are identified by both ids.
"""

import pytest

from tests.handlers.assets.test_createAsset_cross_database_prefix_takeover import (
    _OTHER_DB,
    _OWN_DB,
    _assert_nothing_persisted,
    _load,
    _request_model,
    _wire,
)

ASSET_ID = "chosen-asset"


def _hold(m, rows):
    """Answer the asset table's get_item from `rows`, keyed (databaseId, assetId)."""
    m.asset_table.get_item.side_effect = lambda Key, **kwargs: (
        {"Item": dict(rows[(Key["databaseId"], Key["assetId"])])}
        if (Key["databaseId"], Key["assetId"]) in rows else {})


@pytest.mark.unit
class TestTheChosenIdIsUniqueWithinTheDatabase:
    def test_an_id_an_archived_asset_of_the_database_holds_is_refused(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        _hold(m, {(f"{_OWN_DB}#deleted", ASSET_ID): {"databaseId": f"{_OWN_DB}#deleted", "assetId": ASSET_ID}})

        with pytest.raises(m.VAMSGeneralErrorResponse, match="already exists"):
            m.create_asset(_request_model(m, asset_id=ASSET_ID), {"tokens": ["user1"]})
        _assert_nothing_persisted(m)

    def test_an_id_a_live_asset_of_the_database_holds_is_refused(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        _hold(m, {(_OWN_DB, ASSET_ID): {"databaseId": _OWN_DB, "assetId": ASSET_ID}})

        with pytest.raises(m.VAMSGeneralErrorResponse, match="already exists"):
            m.create_asset(_request_model(m, asset_id=ASSET_ID), {"tokens": ["user1"]})
        _assert_nothing_persisted(m)

    def test_the_same_id_in_another_database_is_accepted(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        _hold(m, {(_OTHER_DB, ASSET_ID): {"databaseId": _OTHER_DB, "assetId": ASSET_ID},
                  (f"{_OTHER_DB}#deleted", ASSET_ID): {"databaseId": f"{_OTHER_DB}#deleted", "assetId": ASSET_ID}})

        response = m.create_asset(_request_model(m, asset_id=ASSET_ID), {"tokens": ["user1"]})

        assert response.assetId == ASSET_ID
        m.save_asset_details.assert_called_once()
        assert m.save_asset_details.call_args.args[0]["databaseId"] == _OWN_DB
