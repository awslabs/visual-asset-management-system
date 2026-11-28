# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanent delete of an asset that does not exist answers 404, as the asset GET does (#1904).

`DELETE /database/{databaseId}/assets/{assetId}/deleteAsset` answered 400 "Asset not found in
database" for a missing asset, so a client read it as a refused delete. An authorized caller now
gets 404 `{"message": "Asset not found"}`. Authorization still decides first: a refused caller gets
the same 403 whether or not the asset exists (pinned in test_assetService_existence_disclosure.py).
"""

import json

import pytest

from tests.handlers.assets.test_assetService_existence_disclosure import _asset_for, _call


_DELETE = ("delete_asset_permanent", "handle_delete_request", "DELETE", "/deleteAsset")
_ARCHIVE = ("archive_asset", "handle_delete_request", "DELETE", "/archiveAsset")


@pytest.mark.unit
class TestPermanentDeleteOfAMissingAsset:
    def test_an_authorized_caller_gets_404_asset_not_found(self):
        response, spy, m = _call(*_DELETE, {"confirmPermanentDelete": True}, None)

        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"message": "Asset not found"}
        assert spy.calls, "the 404 was produced with nothing authorized"
        m.asset_table.delete_item.assert_not_called()

    def test_a_refused_caller_gets_403_for_a_missing_and_an_existing_asset(self):
        body = {"confirmPermanentDelete": True}
        missing, _, _ = _call(*_DELETE, body, None, denied_actions=("DELETE",))
        existing, _, _ = _call(
            *_DELETE, body, _asset_for("delete_asset_permanent"), denied_actions=("DELETE",))

        assert missing["statusCode"] == 403
        assert (missing["statusCode"], missing["body"]) == (existing["statusCode"], existing["body"])

    def test_a_missing_confirmation_keeps_its_400(self):
        """CONTROL: only the missing asset moves to 404; the route's other refusals stay 400."""
        response, _, _ = _call(*_DELETE, {"confirmPermanentDelete": False}, None)

        assert response["statusCode"] == 400
        assert "confirmPermanentDelete" in json.loads(response["body"])["message"]

    def test_archiving_a_missing_asset_keeps_its_400(self):
        """CONTROL: the archive route shares the request handler and is not changed here."""
        response, _, _ = _call(*_ARCHIVE, {"confirmArchive": True}, None)

        assert response["statusCode"] == 400
        assert "Asset not found in database" in json.loads(response["body"])["message"]
