# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset file list, file info, version list, version get and export answer a missing asset with
404, as the asset GET does (issue #1017).

These routes answered 400 ("Asset not found in database." / "Asset not found") for an asset id
that does not exist, while `GET /database/{databaseId}/assets/{assetId}` answers 404 with
`{"message": "Asset not found"}` (pinned in test_assetService_existence_disclosure.py). They now
answer that same 404 to an authorized caller.

To a caller refused the asset (issue #959) a missing asset still answers exactly as an existing
one does, so each route is checked for both callers.
"""

import json

import pytest

from tests.handlers.assets.test_asset_reads_denied_403 import (  # noqa: F401  (route is a fixture)
    ROUTES,
    ROUTE_IDS,
    STORED_ASSET,
    _Enforcer,
    route,
)


@pytest.mark.unit
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS, indirect=True)
class TestAssetReadMissingIs404:
    def test_missing_asset_answers_404_to_an_authorized_caller(self, route):
        _, call, gate_results = route

        response = call(None)

        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"message": "Asset not found"}
        assert gate_results == []
        assert ("asset", "db-a", "xasset-1") in {c[:3] for c in _Enforcer.object_calls}

    def test_missing_asset_answers_a_refused_caller_as_a_refused_existing_one(self, route):
        _, call, _ = route
        _Enforcer.object_verdict = False

        existing = call(STORED_ASSET)
        missing = call(None)

        assert (missing["statusCode"], missing["body"]) == (existing["statusCode"], existing["body"])
        assert missing["statusCode"] == 403

    def test_a_failed_asset_read_keeps_its_400(self, route):
        """Negative control on the mapping: only the missing asset itself moves to 404."""
        _, call, _ = route

        response = call(RuntimeError("table read failed"))

        assert response["statusCode"] == 400
        assert "Error retrieving asset" in json.loads(response["body"])["message"]
