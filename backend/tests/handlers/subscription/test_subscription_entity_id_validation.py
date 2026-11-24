#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""entityId validation on the subscription write routes, and its listing (#1197).

For ``entityName: "Asset"`` the ``entityId`` is an asset ID, so ``POST``, ``PUT`` and
``DELETE /subscriptions`` and ``DELETE /unsubscribe`` validate it with the asset ID rule
(``ASSET_ID``) that asset creation and ``POST /check-subscription`` use. Every asset ID the
asset API accepts, dotted, spaced, longer than 63 characters or ending in ``#`` included,
reaches the asset lookup and the subscription operation; an ID that rule refuses (``#``
anywhere but the last character among them) is answered 400 before any lookup.

A subscription row is keyed ``<entityName>:<databaseId>:<entityId>`` and carries the three as
attributes. ``entityName`` (``OBJECT_NAME``) and ``databaseId`` (``ID``) cannot contain ``:``, so a row
without the attributes is split at its first two ``:`` and the rest is returned whole as the entityId,
whatever ``#`` or ``:`` it contains.
"""

import json

import pytest

from backend.backend.handlers.subscription import subscriptionService
from backend.backend.handlers.subscription import unsubscribeService

DOWNSTREAM_RESPONSE = {"statusCode": 200, "body": json.dumps({"message": "success"})}
DATABASE_ID = "test-database-id"

ACCEPTED_IDS = [
    pytest.param("building-model", id="plain"),
    pytest.param("smk.dotted.u2", id="dotted"),
    pytest.param("x-r.1", id="dotted-short"),
    pytest.param("site 4.rev2", id="space-and-dot"),
    pytest.param("a" * 64, id="64-chars"),
    pytest.param("a" * 255, id="longest-asset-id"),
    pytest.param("abc#", id="trailing-hash"),
]

REFUSED_IDS = [
    pytest.param("ab#c", id="hash-mid-string"),
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

    def __call__(self, database_id, asset_id, include_archived=False):
        self.calls.append((database_id, asset_id))
        return {"assetId": asset_id, "databaseId": database_id, "assetName": "Test Asset"}


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
            "databaseId": DATABASE_ID,
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
        assert lookup.calls == [(DATABASE_ID, entity_id)]
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


class StubScan:
    """Stand-in for the dynamodb client's scan paginator, returning one fixed page."""

    def __init__(self, items):
        self.items = items

    def get_paginator(self, operation):
        assert operation == "scan"
        return self

    def paginate(self, **kwargs):
        return self

    def build_full_result(self):
        return {"Items": self.items}


def _row(entity_id, with_attributes=True):
    # The row create_subscription writes: the sort key and, beside it, the three parts
    row = {
        "eventName": {"S": "Asset Version Change"},
        "entityName:databaseId:entityId": {"S": f"Asset:{DATABASE_ID}:{entity_id}"},
        "subscribers": {"L": [{"S": "test-user@example.com"}]},
    }
    if with_attributes:
        row.update({"entityName": {"S": "Asset"}, "databaseId": {"S": DATABASE_ID},
                    "entityId": {"S": entity_id}})
    return row


def _list(monkeypatch, entity_ids, with_attributes=True):
    monkeypatch.setattr(
        subscriptionService, "request_to_claims", lambda event: {"tokens": ["test-user@example.com"]})
    monkeypatch.setattr(subscriptionService, "CasbinEnforcer", AllowingEnforcer())
    monkeypatch.setattr(subscriptionService, "_read_live_assets", lambda keys: {
        key: {"databaseId": key[0], "assetId": key[1], "assetName": "Test Asset"} for key in keys})
    monkeypatch.setattr(
        subscriptionService, "dynamodb_client", StubScan([_row(i, with_attributes) for i in entity_ids]))
    event = {
        "requestContext": {"http": {"method": "GET", "path": "/subscriptions"}},
        "queryStringParameters": {"maxItems": "10", "pageSize": "10", "startingToken": ""},
    }
    return subscriptionService.lambda_handler(event, None)


@pytest.mark.unit
class TestSubscriptionListingParsesEveryAssetId:

    @pytest.mark.parametrize("entity_id", [
        pytest.param("building-model", id="plain"),
        pytest.param("smk.dotted.u2", id="dotted"),
        pytest.param("abc#", id="trailing-hash"),
        pytest.param("a#", id="shortest-trailing-hash"),
        # Not writable through the API (the asset ID rule refuses it), but the parser
        # still returns everything after the second ':' whole
        pytest.param("ab#c", id="hash-mid-string"),
        pytest.param("ab:c", id="colon-mid-string"),
    ])
    @pytest.mark.parametrize("with_attributes", [True, False], ids=["attributes", "sort-key-only"])
    def test_a_row_lists_with_its_entity_id_whole(self, monkeypatch, entity_id, with_attributes):
        response = _list(monkeypatch, [entity_id], with_attributes)

        assert response["statusCode"] == 200, response["body"]
        items = json.loads(response["body"])["message"]["Items"]
        assert [(i["entityName"], i["databaseId"], i["entityId"]) for i in items] == [
            ("Asset", DATABASE_ID, entity_id)]

    def test_one_trailing_hash_row_does_not_break_the_listing_of_the_others(self, monkeypatch):
        response = _list(monkeypatch, ["building-model", "abc#", "smk.dotted.u2"])

        assert response["statusCode"] == 200, response["body"]
        items = json.loads(response["body"])["message"]["Items"]
        assert [i["entityId"] for i in items] == ["building-model", "abc#", "smk.dotted.u2"]
