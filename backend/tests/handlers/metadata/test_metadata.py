# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the centralized metadata service handler.

Rewritten for the modernized architecture. Metadata handling was consolidated from
three separate modules (handlers.metadata.create / read / delete) into a single
path-routing handler `handlers.metadata.metadataService.lambda_handler`. These tests
target the current handler: Tier-1 API authorization, route dispatch, and
route-not-found behavior.
"""

import base64
import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.metadata.metadataService import (
    lambda_handler,
    paginate_metadata_records,
    DEFAULT_METADATA_PAGE_SIZE,
)


def _event(method, path, body=None, path_params=None):
    evt = {
        "version": "2.0",
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "x.x.x.x",
            },
        },
        "pathParameters": path_params or {},
        "queryStringParameters": {"maxItems": "10", "pageSize": "10", "startingToken": ""},
        "headers": {"authorization": "Bearer test-token"},
        "isBase64Encoded": False,
    }
    if body is not None:
        evt["body"] = json.dumps(body)
    return evt


@patch("backend.backend.handlers.metadata.metadataService.request_to_claims")
@patch("backend.backend.handlers.metadata.metadataService.CasbinEnforcer")
def test_api_authorization_denied(mock_enforcer, mock_claims):
    """Tier-1 API denial returns 403 Not Authorized."""
    mock_claims.return_value = {"tokens": ["test-token"]}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = False
    mock_enforcer.return_value = enforcer_instance

    response = lambda_handler(
        _event("GET", "/database/123/assets/456/metadata",
               path_params={"databaseId": "123", "assetId": "456"}),
        None,
    )

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["message"] == "Not Authorized"
    enforcer_instance.enforceAPI.assert_called_once()


@patch("backend.backend.handlers.metadata.metadataService.request_to_claims")
@patch("backend.backend.handlers.metadata.metadataService.CasbinEnforcer")
def test_no_tokens_denied(mock_enforcer, mock_claims):
    """A request without tokens is denied at the API tier (403)."""
    mock_claims.return_value = {"tokens": []}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True  # should not even be consulted
    mock_enforcer.return_value = enforcer_instance

    response = lambda_handler(
        _event("GET", "/database/123/assets/456/metadata",
               path_params={"databaseId": "123", "assetId": "456"}),
        None,
    )

    assert response["statusCode"] == 403


@patch("backend.backend.handlers.metadata.metadataService.request_to_claims")
@patch("backend.backend.handlers.metadata.metadataService.CasbinEnforcer")
def test_unmatched_route_returns_validation_error(mock_enforcer, mock_claims):
    """An authorized request to a path that matches no metadata route returns 400."""
    mock_claims.return_value = {"tokens": ["test-token"]}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True
    mock_enforcer.return_value = enforcer_instance

    response = lambda_handler(_event("GET", "/totally/unrelated/path"), None)

    assert response["statusCode"] == 400
    assert "Route not found" in json.loads(response["body"])["message"]


@patch("backend.backend.handlers.metadata.metadataService.handle_asset_metadata_get")
@patch("backend.backend.handlers.metadata.metadataService.request_to_claims")
@patch("backend.backend.handlers.metadata.metadataService.CasbinEnforcer")
def test_asset_metadata_get_routes_to_handler(mock_enforcer, mock_claims, mock_handle_get):
    """An authorized asset-metadata GET dispatches to handle_asset_metadata_get."""
    mock_claims.return_value = {"tokens": ["test-token"]}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True
    mock_enforcer.return_value = enforcer_instance
    mock_handle_get.return_value = {"statusCode": 200, "body": json.dumps({"ok": True})}

    event = _event("GET", "/database/123/assets/456/metadata",
                   path_params={"databaseId": "123", "assetId": "456"})
    response = lambda_handler(event, None)

    assert response["statusCode"] == 200
    mock_handle_get.assert_called_once_with(event)


@patch("backend.backend.handlers.metadata.metadataService.handle_database_metadata_get")
@patch("backend.backend.handlers.metadata.metadataService.request_to_claims")
@patch("backend.backend.handlers.metadata.metadataService.CasbinEnforcer")
def test_database_metadata_get_routes_to_handler(mock_enforcer, mock_claims, mock_handle_get):
    """An authorized database-metadata GET (no /assets/) dispatches to the db handler."""
    mock_claims.return_value = {"tokens": ["test-token"]}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True
    mock_enforcer.return_value = enforcer_instance
    mock_handle_get.return_value = {"statusCode": 200, "body": json.dumps({"ok": True})}

    event = _event("GET", "/database/123/metadata", path_params={"databaseId": "123"})
    response = lambda_handler(event, None)

    assert response["statusCode"] == 200
    mock_handle_get.assert_called_once_with(event)


class TestPaginateMetadataRecords:
    """Offset-pagination of the enriched, ordered metadata list (A7)."""

    def test_single_page_when_under_page_size(self):
        records = list(range(5))
        page, next_token = paginate_metadata_records(records, {"pageSize": 10})
        assert page == records
        assert next_token is None

    def test_first_page_emits_next_token(self):
        records = list(range(25))
        page, next_token = paginate_metadata_records(records, {"pageSize": 10})
        assert page == list(range(10))
        assert next_token is not None
        # Token decodes to the next offset
        assert int(base64.b64decode(next_token).decode("utf-8")) == 10

    def test_following_token_returns_next_page(self):
        records = list(range(25))
        _, token1 = paginate_metadata_records(records, {"pageSize": 10})
        page2, token2 = paginate_metadata_records(records, {"pageSize": 10, "startingToken": token1})
        assert page2 == list(range(10, 20))
        assert int(base64.b64decode(token2).decode("utf-8")) == 20

    def test_last_page_has_no_next_token(self):
        records = list(range(25))
        page, token = paginate_metadata_records(records, {"pageSize": 10, "startingToken": base64.b64encode(b"20").decode("utf-8")})
        assert page == list(range(20, 25))
        assert token is None

    def test_invalid_token_serves_first_page(self):
        records = list(range(5))
        page, token = paginate_metadata_records(records, {"pageSize": 10, "startingToken": "not-base64!!"})
        assert page == records
        assert token is None

    def test_defaults_when_no_params(self):
        records = list(range(3))
        page, token = paginate_metadata_records(records, {})
        assert page == records
        assert token is None

    def test_maxitems_used_when_no_pagesize(self):
        records = list(range(25))
        page, token = paginate_metadata_records(records, {"maxItems": 5})
        assert page == list(range(5))
        assert int(base64.b64decode(token).decode("utf-8")) == 5
