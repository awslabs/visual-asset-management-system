# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""commentService answers a Tier-1 (API route) denial with 403, before any asset lookup.

The handler serves GET and DELETE on the asset-scoped comment routes. A request the API-level
check denies is an authorization failure, so it is answered 403 like every other handler's
Tier-1 denial; 405 stays reserved for a method this handler does not serve.
"""

import json

import pytest

from backend.tests.conftest import TestComment  # noqa: F401

import backend.backend.handlers.comments.commentService as commentService

_CALLER = "test_email@amazon.com"
_ASSET_ID = "x-comment-denial-asset"
_VERSION_AND_COMMENT_ID = "test-version-id:test-comment-id"
_PATH = f"/comments/assets/{_ASSET_ID}/assetVersionId:commentId/{_VERSION_AND_COMMENT_ID}"


def _rest_event(method, query=None):
    """An API Gateway REST (v1) proxy event, the shape the handler receives."""
    return {
        "path": _PATH,
        "httpMethod": method,
        "resource": _PATH,
        "headers": {"Content-Type": "application/json"},
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.1"},
            "authorizer": {"jwt": {"claims": {"sub": "test_sub", "email": _CALLER}}},
        },
        "pathParameters": {"assetId": _ASSET_ID, "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
        "queryStringParameters": query,
    }


@pytest.fixture
def reached(monkeypatch):
    """Records every call past Tier-1: an asset lookup, a Tier-2 enforce, or a handler."""
    calls = []

    def _record(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} reached after a Tier-1 denial")

        return _fn

    monkeypatch.setattr(commentService, "get_asset_object_from_id", _record("get_asset_object_from_id"))
    monkeypatch.setattr(commentService, "get_handler", _record("get_handler"))
    monkeypatch.setattr(commentService, "delete_handler", _record("delete_handler"))
    return calls


def _install(monkeypatch, tokens, api_allowed):
    monkeypatch.setattr(commentService, "request_to_claims", lambda event: {"tokens": tokens})

    class _Enforcer:
        def __init__(self, claims_and_roles):
            pass

        def enforceAPI(self, event):
            return api_allowed

        def enforce(self, obj, action=None):
            raise AssertionError("Tier-2 enforce reached after a Tier-1 denial")

    monkeypatch.setattr(commentService, "CasbinEnforcer", _Enforcer)


def _assert_denied(response, reached):
    assert response["statusCode"] == 403
    assert json.loads(response["body"]) == {"message": "Action not allowed"}
    assert _ASSET_ID not in response["body"]
    assert reached == []


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_tier1_denial_is_403(monkeypatch, reached, method):
    _install(monkeypatch, tokens=[_CALLER], api_allowed=False)

    response = commentService.lambda_handler(_rest_event(method), None)

    _assert_denied(response, reached)


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_no_identity_is_403(monkeypatch, reached, method):
    _install(monkeypatch, tokens=[], api_allowed=True)

    response = commentService.lambda_handler(_rest_event(method), None)

    _assert_denied(response, reached)


@pytest.mark.unit
def test_an_unserved_method_is_still_405(monkeypatch, reached):
    _install(monkeypatch, tokens=[_CALLER], api_allowed=True)

    response = commentService.lambda_handler(_rest_event("PUT"), None)

    assert response["statusCode"] == 405
    assert json.loads(response["body"]) == {"message": "Method not allowed"}
    assert reached == []
