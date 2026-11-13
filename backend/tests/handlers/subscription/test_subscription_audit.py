# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The subscription handlers record each action after it succeeded, and nothing when it did not."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.subscription import checkSubscriptionService as check_svc
from backend.backend.handlers.subscription import subscriptionService as svc
from backend.backend.handlers.subscription import unsubscribeService as unsub_svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
ASSET = {"assetId": "a1", "databaseId": "db1", "assetName": "Bracket"}
BODY = {"eventName": "Asset Version Change", "entityName": "Asset", "entityId": "a1", "subscribers": ["bob"]}


def _event(method, body):
    return {"body": json.dumps(body), "queryStringParameters": {},
            "requestContext": {"http": {"method": method, "path": "/subscriptions"}}}


@pytest.fixture
def wired(monkeypatch):
    recorder = MagicMock()
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    enforcer.return_value.enforce.return_value = True
    for module in (svc, unsub_svc, check_svc):
        monkeypatch.setattr(module, "log_action", recorder)
        monkeypatch.setattr(module, "request_to_claims", lambda event: CLAIMS)
        monkeypatch.setattr(module, "CasbinEnforcer", enforcer)
        monkeypatch.setattr(module, "get_asset_object_from_id", lambda database_id, asset_id: dict(ASSET))
    monkeypatch.setattr(svc, "validate_pagination_info", lambda params: None)
    return recorder, enforcer


@pytest.mark.unit
class TestSubscriptionRecords:
    @pytest.mark.parametrize("method, service, action", [
        ("POST", "create_subscription", "subscriptionCreate"),
        ("PUT", "update_subscription", "subscriptionUpdate"),
        ("DELETE", "delete_subscription", "subscriptionDelete")])
    def test_a_write_records_the_subscription(self, wired, monkeypatch, method, service, action):
        recorder, _ = wired
        monkeypatch.setattr(svc, service, lambda body: {"statusCode": 200, "body": "{}"})
        event = _event(method, BODY)
        assert svc.lambda_handler(event, None)["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, action)
        kwargs = recorder.call_args.kwargs
        assert kwargs["database_id"] == "db1" and kwargs["asset_id"] == "a1"
        assert kwargs["changes"]["subscribers"] == ["bob"] and kwargs["item_count"] == 1

    def test_a_write_that_failed_records_nothing(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(svc, "create_subscription", lambda body: {"statusCode": 400, "body": "{}"})
        assert svc.lambda_handler(_event("POST", BODY), None)["statusCode"] == 400
        recorder.assert_not_called()

    def test_a_denied_write_records_nothing(self, wired):
        recorder, enforcer = wired
        enforcer.return_value.enforce.return_value = False
        assert svc.lambda_handler(_event("POST", BODY), None)["statusCode"] == 403
        recorder.assert_not_called()

    def test_an_unsubscribe_is_recorded(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(unsub_svc, "delete_subscription", lambda body: {"statusCode": 200, "body": "{}"})
        event = _event("DELETE", BODY)
        event["requestContext"]["http"]["path"] = "/unsubscribe"
        assert unsub_svc.lambda_handler(event, None)["statusCode"] == 200
        assert recorder.call_args.args == (event, "subscriptionUnsubscribe")

    def test_a_check_records_the_request(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(check_svc, "check_subscriptions", lambda body: {"statusCode": 200, "body": "{}"})
        event = _event("POST", {"userId": "bob", "assetId": "a1"})
        assert check_svc.lambda_handler(event, None)["statusCode"] == 200
        assert recorder.call_args.args == (event, "subscriptionCheck")
        assert recorder.call_args.kwargs["request_parameters"] == {"userId": "bob", "assetId": "a1"}
