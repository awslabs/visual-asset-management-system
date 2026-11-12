#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""entityId validation on the subscription write routes (#1197).

For ``entityName: "Asset"`` the ``entityId`` is an asset ID, so ``POST``, ``PUT`` and
``DELETE /subscriptions`` and ``DELETE /unsubscribe`` validate it with the asset ID rule
(``ASSET_ID``) that asset creation and ``POST /check-subscription`` use. Every asset ID the
asset API accepts, dotted, spaced or longer than 63 characters included, reaches the asset
lookup and the subscription operation; an ID that rule refuses is answered 400 before any
lookup. ``#`` stays refused, which keeps the ``entityName#entityId`` row key unambiguous.
"""

import json

import pytest

from backend.backend.handlers.subscription import subscriptionService
from backend.backend.handlers.subscription import unsubscribeService

DOWNSTREAM_RESPONSE = {"statusCode": 200, "body": json.dumps({"message": "success"})}

ACCEPTED_IDS = [
    pytest.param("building-model", id="plain"),
    pytest.param("smk.dotted.u2", id="dotted"),
    pytest.param("x-r.1", id="dotted-short"),
    pytest.param("site 4.rev2", id="space-and-dot"),
    pytest.param("a" * 64, id="64-chars"),
    pytest.param("a" * 255, id="longest-asset-id"),
]

REFUSED_IDS = [
    pytest.param("ab#c", id="hash"),
    pytest.param("a/b", id="slash"),
    pytest.param("trailing.", id="trailing-dot"),
    pytest.param("trailing ", id="trailing-space"),
    pytest.param("a" * 256, id="too-long"),
    pytest.param("a", id="too-short"),
]

# (module, http method, path, the operation the handler dispatches to)
WRITE_ROUTES = [
    pytest.param(subscriptionService, "POST", "/subscriptions", "create_subscription",
                 id="POST-subscriptions"),
    pytest.param(subscriptionService, "PUT", "/subscriptions", "update_subscription",
                 id="PUT-subscriptions"),
    pytest.param(subscriptionService, "DELETE", "/subscriptions", "delete_subscription",
                 id="DELETE-subscriptions"),
    pytest.param(unsubscribeService, "DELETE", "/unsubscribe", "delete_subscription",
                 id="DELETE-unsubscribe"),
]


class AllowingEnforcer:
    """Stand-in for CasbinEnforcer that allows both tiers."""

    def __call__(self, claims_and_roles):
        return self

    def enforceAPI(self, event):
        return True

    def enforce(self, asset_object, action):
        return True


class RecordingLookup:
    """Stand-in for get_asset_object_from_id resolving any assetId to a live asset."""

    def __init__(self):
        self.calls = []

    def __call__(self, database_id, asset_id):
        self.calls.append((database_id, asset_id))
        return {"assetId": asset_id, "databaseId": "test-database-id", "assetName": "Test Asset"}


class RecordingCall:
    """Stand-in for the downstream subscription operation, recording its invocations."""

    def __init__(self):
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        return DOWNSTREAM_RESPONSE


def _call(monkeypatch, module, method, path, operation, entity_id):
    monkeypatch.setattr(
        module, "request_to_claims", lambda event: {"tokens": ["test-user@example.com"]})
    monkeypatch.setattr(module, "CasbinEnforcer", AllowingEnforcer())
    lookup = RecordingLookup()
    monkeypatch.setattr(module, "get_asset_object_from_id", lookup)
    downstream = RecordingCall()
    monkeypatch.setattr(module, operation, downstream)
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": {},
        "body": {
            "eventName": "Asset Version Change",
            "entityName": "Asset",
            "entityId": entity_id,
            "subscribers": ["test-user@example.com"],
        },
    }
    return module.lambda_handler(event, None), lookup, downstream


@pytest.mark.unit
@pytest.mark.parametrize("module, method, path, operation", WRITE_ROUTES)
class TestSubscriptionEntityIdFollowsTheAssetIdRule:

    @pytest.mark.parametrize("entity_id", ACCEPTED_IDS)
    def test_an_asset_id_the_asset_api_accepts_is_dispatched(
            self, monkeypatch, module, method, path, operation, entity_id):
        response, lookup, downstream = _call(
            monkeypatch, module, method, path, operation, entity_id)

        assert response["statusCode"] == 200, response["body"]
        assert lookup.calls == [(None, entity_id)]
        assert [body["entityId"] for body in downstream.bodies] == [entity_id]

    @pytest.mark.parametrize("entity_id", REFUSED_IDS)
    def test_an_id_the_asset_id_rule_refuses_is_answered_400_before_the_lookup(
            self, monkeypatch, module, method, path, operation, entity_id):
        response, lookup, downstream = _call(
            monkeypatch, module, method, path, operation, entity_id)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].startswith("entityId ")
        assert lookup.calls == []
        assert downstream.bodies == []
