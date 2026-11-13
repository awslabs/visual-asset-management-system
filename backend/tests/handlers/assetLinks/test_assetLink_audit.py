# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset link handlers record each action after it succeeded, with both ends of the link."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assetLinks import assetLinksService as svc
from backend.backend.handlers.assetLinks import createAssetLink as create_svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
LINK_ID = "11111111-2222-4333-8444-555555555555"
LINK = {"assetLinkId": LINK_ID, "fromAssetId": "asset-from", "fromAssetDatabaseId": "db-a",
        "toAssetId": "asset-to", "toAssetDatabaseId": "db-b", "relationshipType": "related", "tags": ["old"]}


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    for module in (svc, create_svc):
        monkeypatch.setattr(module, "log_action", recorder)
        monkeypatch.setattr(module, "claims_and_roles", CLAIMS, raising=False)
    return recorder


@pytest.fixture
def link_table(monkeypatch):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(LINK)}
    monkeypatch.setattr(svc, "asset_links_table", table, raising=False)
    monkeypatch.setattr(svc, "get_asset_details", lambda asset_id, database_id: {"assetId": asset_id,
                                                                                  "databaseId": database_id})
    monkeypatch.setattr(svc, "check_asset_permission", lambda asset, claims, action="GET": True)
    monkeypatch.setattr(svc, "delete_asset_link_metadata", lambda link_id: None)
    return table


@pytest.mark.unit
class TestAssetLinkRecords:
    def test_an_update_records_the_change_and_both_ends(self, recorder, link_table):
        event = {"pathParameters": {"assetLinkId": LINK_ID}}
        svc.update_asset_link(LINK_ID, svc.UpdateAssetLinkRequestModel(tags=["new"]), CLAIMS, event=event)
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "assetLinkUpdate")
        assert (kwargs["database_id"], kwargs["asset_id"]) == ("db-a", "asset-from")
        assert (kwargs["targetDatabaseId"], kwargs["targetAssetId"]) == ("db-b", "asset-to")
        assert kwargs["changes"] == {"tags": ["new"]} and kwargs["previous"] == {"tags": ["old"]}

    def test_a_denied_update_records_nothing(self, recorder, link_table, monkeypatch):
        monkeypatch.setattr(svc, "check_asset_permission", lambda asset, claims, action="GET": False)
        with pytest.raises(Exception):
            svc.update_asset_link(LINK_ID, svc.UpdateAssetLinkRequestModel(tags=["new"]), CLAIMS, event={})
        recorder.assert_not_called()

    def test_a_delete_records_both_ends(self, recorder, link_table):
        event = {"pathParameters": {"assetLinkId": LINK_ID}}
        svc.delete_asset_link(LINK_ID, CLAIMS, event=event)
        recorder.assert_called_once_with(event, "assetLinkDelete", assetLinkId=LINK_ID, database_id="db-a",
                                         asset_id="asset-from", targetDatabaseId="db-b", targetAssetId="asset-to")

    def test_a_delete_of_a_missing_link_records_nothing(self, recorder, link_table):
        link_table.get_item.return_value = {}
        with pytest.raises(Exception):
            svc.delete_asset_link(LINK_ID, CLAIMS, event={})
        recorder.assert_not_called()

    def test_a_create_records_the_written_link(self, recorder, monkeypatch):
        monkeypatch.setattr(create_svc, "create_asset_link", lambda model, claims: create_svc.CreateAssetLinkResponseModel(
            assetLinkId=LINK_ID, message="created"))
        body = {k: LINK[k] for k in ("fromAssetId", "fromAssetDatabaseId", "toAssetId", "toAssetDatabaseId",
                                      "relationshipType")}
        event = {"body": json.dumps(body)}
        assert create_svc.handle_post_request(event)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "assetLinkCreate")
        assert kwargs["assetLinkId"] == LINK_ID and kwargs["targetAssetId"] == "asset-to"
        assert kwargs["changes"]["fromAssetId"] == "asset-from"

    def test_a_get_records_the_link_id(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "get_single_asset_link", lambda link_id, claims: MagicMock(dict=lambda: {}))
        event = {"pathParameters": {"assetLinkId": LINK_ID}, "queryStringParameters": {}}
        assert svc.handle_get_request(event)["statusCode"] == 200
        recorder.assert_called_once_with(event, "assetLinkGet", assetLinkId=LINK_ID)

    def test_a_refused_get_records_nothing(self, recorder, monkeypatch):
        def refuse(link_id, claims):
            raise PermissionError("Not authorized")
        monkeypatch.setattr(svc, "get_single_asset_link", refuse)
        event = {"pathParameters": {"assetLinkId": LINK_ID}, "queryStringParameters": {}}
        assert svc.handle_get_request(event)["statusCode"] == 403
        recorder.assert_not_called()
