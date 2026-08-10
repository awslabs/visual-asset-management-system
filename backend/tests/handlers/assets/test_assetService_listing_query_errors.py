# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Caller-safe messages on the asset-listing query-parameter error paths (backend Rule 11).

Both asset listings -- the per-database listing and the all-databases listing -- parse their
pagination query parameters with GetAssetsRequestModel. A rejected value must produce the same
sanitized message every other ValidationError arm in the handler produces: no internal model class
name, no pydantic taxonomy token, no constraint limit.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")

# Everything that must never appear in a response body.
LEAK_MARKERS = ("GetAssetsRequestModel", "type=", "limit_value", "__root__",
                "validation error for", "Invalid parameter:")


def _get(query_parameters, path_parameters):
    """Run handle_get_request and return (statusCode, parsed body)."""
    from backend.tests.handlers.assets.test_assetService_history import _load

    m = _load()
    event = {
        'requestContext': {'http': {'method': 'GET', 'path': '/assets'}},
        'pathParameters': path_parameters,
        'queryStringParameters': query_parameters,
    }
    m.claims_and_roles = {"tokens": ["u1"]}
    with patch.object(m, 'get_assets', MagicMock()) as mock_db_list, \
            patch.object(m, 'get_all_assets', MagicMock()) as mock_all_list:
        response = m.handle_get_request(event)

    # A rejected parameter must never reach the query layer.
    mock_db_list.assert_not_called()
    mock_all_list.assert_not_called()
    return response['statusCode'], json.loads(response['body'])


@pytest.mark.unit
class TestDatabaseAssetListingQueryErrors:
    @pytest.mark.parametrize("query_parameters", [
        {"maxItems": "0"},
        {"pageSize": "abc"},
    ])
    def test_rejected_parameter_message_carries_no_internal_detail(self, query_parameters):
        status, body = _get(query_parameters, {'databaseId': 'db1'})
        assert status == 400
        message = body['message']
        for marker in LEAK_MARKERS:
            assert marker not in message, f"{marker!r} leaked: {message!r}"

    def test_the_field_name_survives_so_the_error_stays_actionable(self):
        _, body = _get({"maxItems": "0"}, {'databaseId': 'db1'})
        assert "maxItems" in body['message']


@pytest.mark.unit
class TestAllAssetsListingQueryErrors:
    @pytest.mark.parametrize("query_parameters", [
        {"maxItems": "0"},
        {"pageSize": "abc"},
    ])
    def test_rejected_parameter_message_carries_no_internal_detail(self, query_parameters):
        status, body = _get(query_parameters, {})
        assert status == 400
        message = body['message']
        for marker in LEAK_MARKERS:
            assert marker not in message, f"{marker!r} leaked: {message!r}"

    def test_the_field_name_survives_so_the_error_stays_actionable(self):
        _, body = _get({"pageSize": "abc"}, {})
        assert "pageSize" in body['message']


@pytest.mark.unit
class TestListingQueryParametersControl:
    """POSITIVE CONTROL: a valid page request reaches the query layer.

    Without this, an unconditional 400 (a wrong patch target, a broken event shape) would make
    every assertion above pass vacuously.
    """

    def test_valid_pagination_reaches_the_query_layer(self):
        from backend.tests.handlers.assets.test_assetService_history import _load

        m = _load()
        event = {
            'requestContext': {'http': {'method': 'GET', 'path': '/assets'}},
            'pathParameters': {},
            'queryStringParameters': {"maxItems": "10", "pageSize": "10"},
        }
        m.claims_and_roles = {"tokens": ["u1"]}
        with patch.object(m, 'get_all_assets', return_value={'Items': []}) as mock_all_list:
            response = m.handle_get_request(event)

        assert response['statusCode'] == 200
        mock_all_list.assert_called_once()
