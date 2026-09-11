#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset-not-found handling in the subscription handlers (S2-BACKEND-077 / FIX-033).

``common.dynamodb.get_asset_object_from_id`` returns ``None`` when an assetId resolves to
no live asset. ``checkSubscriptionService`` and ``unsubscribeService`` annotate the
returned record with ``object__type`` before authorizing against it, so both need a
not-found guard ahead of that annotation: without one, ``None.update(...)`` raises
AttributeError and the handler answers 500 instead of 404.

The 404 is only safe behind Tier-1 authorization. Both handlers therefore complete the
route check (``enforceAPI``) before the asset lookup, matching the ordering in
``subscriptionService``; Tier-2 ``enforce`` needs the asset record and necessarily stays
after it. An unauthorized caller consequently receives the same 403 whether or not the
asset exists, so the 404 cannot be used to probe the asset namespace.

Each not-found case is paired with a positive control that resolves a real asset record,
so a guard that answers 404 unconditionally fails the pair. The not-found cases also
assert that Tier-2 enforcement never ran, pinning the guard ahead of the annotation, and
that the response body does not echo the requested id (backend Rule 11).
"""

import json

import pytest

from backend.backend.handlers.subscription import checkSubscriptionService
from backend.backend.handlers.subscription import unsubscribeService

ASSET_ID = "test-asset-id"

# Shape returned by the no-databaseId lookup path for a live asset.
ASSET_OBJECT = {
    "assetId": ASSET_ID,
    "assetName": "Test Asset",
    "databaseId": "test-database-id",
    "assetType": ".glb",
    "tags": [],
}

DOWNSTREAM_RESPONSE = {"statusCode": 200, "body": json.dumps({"message": "success"})}


class RecordingEnforcer:
    """Stand-in for CasbinEnforcer recording construction and each enforcement tier."""

    def __init__(self, api_allowed=True):
        self.api_allowed = api_allowed
        self.constructed = 0
        self.api_calls = 0
        self.object_calls = 0

    def __call__(self, claims_and_roles):
        self.constructed += 1
        return self

    def enforceAPI(self, event):
        self.api_calls += 1
        return self.api_allowed

    def enforce(self, asset_object, action):
        self.object_calls += 1
        return True


class RecordingLookup:
    """Stand-in for get_asset_object_from_id returning a fixed result and recording calls."""

    def __init__(self, asset_object):
        self.asset_object = asset_object
        self.calls = []

    def __call__(self, database_id, asset_id):
        self.calls.append((database_id, asset_id))
        return self.asset_object


class RecordingCall:
    """Stand-in for the downstream subscription operation, recording its invocations."""

    def __init__(self):
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        return DOWNSTREAM_RESPONSE


def _patch_handler(monkeypatch, module, downstream_name, asset_object, api_allowed=True):
    """Wire a subscription handler to a fixed asset-lookup result and record collaborators."""
    monkeypatch.setattr(
        module, "request_to_claims", lambda event: {"tokens": ["test-user@example.com"]})
    lookup = RecordingLookup(asset_object)
    monkeypatch.setattr(module, "get_asset_object_from_id", lookup)
    enforcer = RecordingEnforcer(api_allowed=api_allowed)
    monkeypatch.setattr(module, "CasbinEnforcer", enforcer)
    downstream = RecordingCall()
    monkeypatch.setattr(module, downstream_name, downstream)
    return enforcer, downstream, lookup


def _check_subscription_event():
    return {
        "requestContext": {"http": {"method": "POST", "path": "/subscriptions/check"}},
        "body": {
            "userId": "test-user@example.com",
            "assetId": ASSET_ID,
        },
    }


def _unsubscribe_event():
    return {
        "requestContext": {"http": {"method": "DELETE", "path": "/unsubscribe"}},
        "body": {
            "eventName": "Asset Version Change",
            "entityName": "Asset",
            "entityId": ASSET_ID,
            "subscribers": ["test-user@example.com"],
        },
    }


def _unauthorized_responses(monkeypatch, module, downstream_name, event_factory):
    """Call a handler as a Tier-1-denied caller for a missing then an existing asset."""
    responses = []
    lookups = []
    for asset_object in (None, dict(ASSET_OBJECT)):
        _enforcer, downstream, lookup = _patch_handler(
            monkeypatch, module, downstream_name, asset_object, api_allowed=False)
        responses.append(module.lambda_handler(event_factory(), None))
        assert downstream.bodies == []
        lookups.append(lookup.calls)
    return responses, lookups


@pytest.mark.unit
class TestCheckSubscriptionServiceAssetLookup:
    """checkSubscriptionService resolves the asset by assetId after the route check."""

    def test_unresolvable_asset_returns_404(self, monkeypatch):
        enforcer, downstream, lookup = _patch_handler(
            monkeypatch, checkSubscriptionService, "check_subscriptions", None)

        response = checkSubscriptionService.lambda_handler(_check_subscription_event(), None)

        assert response["statusCode"] == 404
        assert enforcer.api_calls == 1, "route check must run before the asset lookup"
        assert enforcer.object_calls == 0, "guard must run before object-level enforcement"
        assert lookup.calls == [(None, ASSET_ID)]
        assert downstream.bodies == []
        assert ASSET_ID not in response["body"], "response must not echo the requested id"

    def test_resolvable_asset_is_authorized(self, monkeypatch):
        enforcer, downstream, lookup = _patch_handler(
            monkeypatch, checkSubscriptionService, "check_subscriptions", dict(ASSET_OBJECT))

        response = checkSubscriptionService.lambda_handler(_check_subscription_event(), None)

        assert response["statusCode"] == 200
        assert enforcer.constructed == 1
        assert enforcer.api_calls == 1
        assert enforcer.object_calls == 1
        assert lookup.calls == [(None, ASSET_ID)]
        assert len(downstream.bodies) == 1


@pytest.mark.unit
class TestUnsubscribeServiceAssetLookup:
    """unsubscribeService resolves the asset by entityId after the route check."""

    def test_unresolvable_asset_returns_404(self, monkeypatch):
        enforcer, downstream, lookup = _patch_handler(
            monkeypatch, unsubscribeService, "delete_subscription", None)

        response = unsubscribeService.lambda_handler(_unsubscribe_event(), None)

        assert response["statusCode"] == 404
        assert enforcer.api_calls == 1, "route check must run before the asset lookup"
        assert enforcer.object_calls == 0, "guard must run before object-level enforcement"
        assert lookup.calls == [(None, ASSET_ID)]
        assert downstream.bodies == []
        assert ASSET_ID not in response["body"], "response must not echo the requested id"

    def test_resolvable_asset_is_authorized(self, monkeypatch):
        enforcer, downstream, lookup = _patch_handler(
            monkeypatch, unsubscribeService, "delete_subscription", dict(ASSET_OBJECT))

        response = unsubscribeService.lambda_handler(_unsubscribe_event(), None)

        assert response["statusCode"] == 200
        assert enforcer.constructed == 1
        assert enforcer.api_calls == 1
        assert enforcer.object_calls == 1
        assert lookup.calls == [(None, ASSET_ID)]
        assert len(downstream.bodies) == 1


@pytest.mark.unit
class TestSubscriptionAssetExistenceIsNotObservableWhenUnauthorized:
    """A Tier-1-denied caller gets one indistinguishable 403 for missing and existing assets."""

    def test_check_subscription_denies_identically(self, monkeypatch):
        (missing, existing), lookups = _unauthorized_responses(
            monkeypatch, checkSubscriptionService, "check_subscriptions",
            _check_subscription_event)

        assert [missing["statusCode"], existing["statusCode"]] == [403, 403]
        assert missing["body"] == existing["body"], (
            "a differing body would reveal whether the asset exists")
        assert lookups == [[], []], "the asset lookup must not run for a denied caller"

    def test_unsubscribe_denies_identically(self, monkeypatch):
        (missing, existing), lookups = _unauthorized_responses(
            monkeypatch, unsubscribeService, "delete_subscription", _unsubscribe_event)

        assert [missing["statusCode"], existing["statusCode"]] == [403, 403]
        assert missing["body"] == existing["body"], (
            "a differing body would reveal whether the asset exists")
        assert lookups == [[], []], "the asset lookup must not run for a denied caller"
