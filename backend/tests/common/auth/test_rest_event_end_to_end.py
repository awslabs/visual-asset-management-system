# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end check that the REST API v1 event shim works through request_to_claims.

Every VAMS handler calls ``request_to_claims(event)`` as its first action (gold-standard
pattern), and ``request_to_claims`` calls ``normalize_event`` first. Under the REST API
(v1) proxy integration, the event has top-level ``httpMethod``/``path`` and a flat
``requestContext.authorizer`` string map instead of the HTTP API (v2) ``requestContext.http``
block and nested ``authorizer.jwt``/``authorizer.lambda``. These tests prove that after
``request_to_claims`` runs, the SAME event object a handler then reads carries the canonical
``requestContext.http.{path,method,sourceIp}`` shape — i.e. the migration shim is wired
end-to-end and handlers need no per-handler change. The v2 case is included to prove
backward compatibility is preserved.
"""
import pytest
from backend.backend.handlers.auth import request_to_claims


@pytest.mark.unit
def test_rest_v1_event_normalized_in_place_and_claims_extracted():
    """REST v1 proxy event → request_to_claims mutates it to the canonical v2 shape and
    extracts claims from the flat authorizer map (what a handler relies on)."""
    rest_event = {
        "httpMethod": "GET",
        "path": "/database/db1/assets",
        "resource": "/database/{databaseId}/assets",
        "pathParameters": {"databaseId": "db1"},
        "requestContext": {
            "identity": {"sourceIp": "203.0.113.7"},
            "authorizer": {
                "sub": "u1",
                "vams:tokens": '["u1"]',
                "vams:roles": '["admin"]',
            },
        },
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer x"},
    }

    claims_and_roles = request_to_claims(rest_event)

    # The handler reads these directly (e.g. assetService reads
    # event['requestContext']['http']['path'] / ['method']) — they must exist post-call.
    http_ctx = rest_event["requestContext"]["http"]
    assert http_ctx["path"] == "/database/db1/assets"
    assert http_ctx["method"] == "GET"
    assert http_ctx["sourceIp"] == "203.0.113.7"
    # Claims came from the flat REST authorizer map.
    assert claims_and_roles["tokens"] == ["u1"]
    assert claims_and_roles["roles"] == ["admin"]


@pytest.mark.unit
def test_http_v2_event_still_works_unchanged():
    """HTTP API v2 event (pre-migration shape) must still work after the shim is in place."""
    v2_event = {
        "requestContext": {
            "http": {"method": "POST", "path": "/database"},
            "authorizer": {
                "lambda": {
                    "sub": "u2",
                    "vams:tokens": '["u2"]',
                    "vams:roles": '["pipeline"]',
                }
            },
        },
        "headers": {"authorization": "Bearer x"},
    }

    claims_and_roles = request_to_claims(v2_event)

    # v2 already has requestContext.http — must be untouched.
    assert v2_event["requestContext"]["http"]["path"] == "/database"
    assert v2_event["requestContext"]["http"]["method"] == "POST"
    assert claims_and_roles["tokens"] == ["u2"]
    assert claims_and_roles["roles"] == ["pipeline"]


@pytest.mark.unit
def test_lambda_cross_call_event_not_corrupted():
    """Internal lambdaCrossCall events must short-circuit without an http block injected."""
    cross_call = {"lambdaCrossCall": {"userName": "SYSTEM_USER"}}

    claims_and_roles = request_to_claims(cross_call)

    assert "http" not in cross_call.get("requestContext", {})
    assert claims_and_roles["tokens"] == ["SYSTEM_USER"]
