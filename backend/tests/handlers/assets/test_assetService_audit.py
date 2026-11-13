# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset handlers record each action after it succeeded, and nothing when it did not.

An edit records the fields it wrote and their prior values from the record it already read for its
check; a read records what was asked and the number of items returned.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.assets.test_assetService_history import _asset, _load

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}


@pytest.fixture
def service(monkeypatch):
    m = _load()
    recorder = MagicMock()
    monkeypatch.setattr(m, "log_action", recorder)
    monkeypatch.setattr(m, "claims_and_roles", CLAIMS, raising=False)
    table = MagicMock()
    table.get_item.return_value = {"Item": _asset(assetName="Old name")}
    monkeypatch.setattr(m, "asset_table", table)
    for name in ("write_asset_history_record", "send_subscription_email", "archive_multi_assetFiles",
                 "archive_file_preview", "update_asset_count"):
        monkeypatch.setattr(m, name, MagicMock())
    monkeypatch.setattr(m, "to_update_expr", lambda updates: ({}, {}, "SET x = :x"), raising=False)
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    monkeypatch.setattr(m, "CasbinEnforcer", enforcer)
    return m, recorder, enforcer


def _event(method, path, body=None, query=None):
    return {"requestContext": {"http": {"method": method, "path": path}},
            "pathParameters": {"databaseId": "db1", "assetId": "a1"}, "queryStringParameters": query or {},
            "body": json.dumps(body) if body is not None else None}


@pytest.mark.unit
class TestAssetRecords:
    def test_an_update_records_the_change_and_the_prior_value(self, service):
        m, recorder, _ = service
        event = _event("PUT", "/database/db1/assets/a1")
        m.update_asset("db1", "a1", {"assetName": "New name"}, CLAIMS, event=event)
        recorder.assert_called_once_with(event, "assetUpdate", database_id="db1", asset_id="a1",
                                         changes={"assetName": "New name"}, previous={"assetName": "Old name"})

    def test_a_denied_update_records_nothing(self, service):
        m, recorder, enforcer = service
        enforcer.return_value.enforce.return_value = False
        with pytest.raises(Exception):
            m.update_asset("db1", "a1", {"assetName": "New name"}, CLAIMS, event={})
        recorder.assert_not_called()

    def test_an_archive_records_the_request(self, service, monkeypatch):
        m, recorder, _ = service
        monkeypatch.setattr(m, "archive_asset", lambda *args: MagicMock(dict=lambda: {}))
        event = _event("DELETE", "/database/db1/assets/a1/archiveAsset", {"confirmArchive": True, "reason": "dup"})
        assert m.handle_delete_request(event)["statusCode"] == 200
        recorder.assert_called_once_with(event, "assetArchive", database_id="db1", asset_id="a1",
                                         changes={"confirmArchive": True, "reason": "dup"})

    def test_a_permanent_delete_is_recorded(self, service, monkeypatch):
        m, recorder, _ = service
        monkeypatch.setattr(m, "delete_asset_permanent", lambda *args: MagicMock(dict=lambda: {}))
        event = _event("DELETE", "/database/db1/assets/a1/deleteAsset", {"confirmPermanentDelete": True})
        assert m.handle_delete_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "assetDelete")

    def test_a_refused_archive_records_nothing(self, service, monkeypatch):
        m, recorder, _ = service

        def refuse(*args):
            raise m.AuthorizationDenied()
        monkeypatch.setattr(m, "archive_asset", refuse)
        event = _event("DELETE", "/database/db1/assets/a1/archiveAsset", {"confirmArchive": True})
        assert m.handle_delete_request(event)["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_list_records_the_request_and_the_count(self, service, monkeypatch):
        m, recorder, _ = service
        monkeypatch.setattr(m, "get_all_assets", lambda params, show: {"Items": [_asset(), _asset(aid="a2")]})
        monkeypatch.setattr(m, "enrich_asset_listing_page", lambda items: items)
        monkeypatch.setattr(m, "format_asset_response", lambda item: {"assetId": item["assetId"]})
        event = {"requestContext": {"http": {"method": "GET", "path": "/assets"}}, "pathParameters": {},
                 "queryStringParameters": {"maxItems": "10"}}
        assert m.handle_get_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "assetList")
        assert recorder.call_args.kwargs["item_count"] == 2
