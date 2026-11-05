# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset file list, file info, version list, version get and export answer an object-level
denial with 403, not 400 (issue #959).

A caller may hold the api route (Tier 1) and still be refused the asset itself (Tier 2). These
routes raised the refusal as a plain `VAMSGeneralErrorResponse`, which their handlers answer with
`general_error` (400), so clients reported a permission refusal as an invalid request while
`GET /database/{databaseId}/assets/{assetId}` answered the same refusal with 403.

The refusal is decided before existence, as on the asset GET: a refused caller receives the same
status and body whether or not the asset exists. Each route is therefore checked for the existing
and the missing asset, and paired with the authorized caller, who must still get past the
object check, because "403" alone is satisfied by a handler that refuses everything.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assets import assetFiles as _assetFiles
from backend.backend.handlers.assets import assetVersions as _assetVersions
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (
    _load_asset_export_service,
)

DATABASE_ID = "db-a"
ASSET_ID = "xasset-1"
VERSION_ID = "1"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["database-user"], "mfaEnabled": False}
STORED_ASSET = {
    "databaseId": DATABASE_ID,
    "assetId": ASSET_ID,
    "assetName": "asset one",
    "bucketId": "bucket-1",
    "assetLocation": {"Key": f"{ASSET_ID}/"},
    "currentVersionId": VERSION_ID,
}


def _event(method, path_suffix, query=None, body=None, extra_path_params=None):
    path_params = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, **(extra_path_params or {})}
    path = f"/database/{DATABASE_ID}/assets/{ASSET_ID}{path_suffix}"
    return {
        "requestContext": {"http": {"method": method, "path": path}, "requestId": "test-request-id"},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "headers": {"Authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


# (route id, module loader, handler name, event)
ROUTES = [
    ("file_list", lambda: _assetFiles, "handle_list_files", _event("GET", "/listFiles")),
    (
        "file_info",
        lambda: _assetFiles,
        "handle_file_info",
        _event("GET", "/fileInfo", query={"filePath": "/model.glb"}),
    ),
    ("version_list", lambda: _assetVersions, "handle_get_versions", _event("GET", "/getVersions")),
    (
        "version_get",
        lambda: _assetVersions,
        "handle_get_version",
        _event("GET", f"/getVersion/{VERSION_ID}", extra_path_params={"assetVersionId": VERSION_ID}),
    ),
    ("export", _load_asset_export_service, "handle_post_export", _event("POST", "/export", body={})),
]
ROUTE_IDS = [route[0] for route in ROUTES]


class _PastTheGate(Exception):
    """Raised right after the object check returned, so the test needs no downstream fakes."""


class _Enforcer:
    """Allows the api route (Tier 1) and answers the object check (Tier 2) with `object_verdict`."""

    object_verdict = True
    object_calls = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, act):
        _Enforcer.object_calls.append((obj.get("object__type"), obj.get("databaseId"), obj.get("assetId"), act))
        return _Enforcer.object_verdict


@pytest.fixture
def route(request, monkeypatch):
    route_id, load, handler_name, event = request.param
    module = load()
    _Enforcer.object_verdict = True
    _Enforcer.object_calls = []
    monkeypatch.setattr(module, "CasbinEnforcer", _Enforcer)
    monkeypatch.setattr(module, "request_to_claims", lambda event: AUTHENTICATED)
    # assetExportService reads the caller from the module global lambda_handler sets
    monkeypatch.setattr(module, "claims_and_roles", AUTHENTICATED, raising=False)

    table = MagicMock()
    monkeypatch.setattr(module, "asset_table", table)

    gate_results = []
    real_gate = module.get_asset_with_permissions

    def gate(*args, **kwargs):
        gate_results.append(real_gate(*args, **kwargs))
        raise _PastTheGate()

    monkeypatch.setattr(module, "get_asset_with_permissions", gate)

    def call(stored_asset):
        """Answer the request with `stored_asset` as the stored record (None: no record; an
        exception: the table read fails with it)."""
        if isinstance(stored_asset, Exception):
            table.get_item.side_effect = stored_asset
        else:
            table.get_item.side_effect = None
            table.get_item.return_value = {"Item": dict(stored_asset)} if stored_asset else {}
        return getattr(module, handler_name)(json.loads(json.dumps(event)), MagicMock())

    return route_id, call, gate_results


@pytest.mark.unit
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS, indirect=True)
class TestAssetReadDenialIs403:
    def test_authorized_caller_passes_the_object_check(self, route):
        """Positive control: the permitted caller reaches the work after the check."""
        _, call, gate_results = route

        call(STORED_ASSET)

        assert len(gate_results) == 1
        assert gate_results[0]["assetId"] == ASSET_ID
        assert ("asset", DATABASE_ID, ASSET_ID) in {c[:3] for c in _Enforcer.object_calls}

    def test_refused_existing_asset_answers_403(self, route):
        _, call, gate_results = route
        _Enforcer.object_verdict = False

        response = call(STORED_ASSET)

        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"message": "Not Authorized"}
        assert gate_results == []

    def test_refused_missing_asset_answers_exactly_as_a_refused_existing_one(self, route):
        _, call, _ = route
        _Enforcer.object_verdict = False

        existing = call(STORED_ASSET)
        missing = call(None)

        assert missing["statusCode"] == existing["statusCode"] == 403
        assert missing["body"] == existing["body"]
        # The missing asset was decided through the identifiers the request supplied
        assert ("asset", DATABASE_ID, ASSET_ID) in {c[:3] for c in _Enforcer.object_calls}
