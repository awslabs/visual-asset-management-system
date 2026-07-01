# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression test for REST API (v1) event shape compatibility.

Confirms that handlers can process REST API proxy events (flat httpMethod/path,
flat authorizer.claims) without KeyError on requestContext['http']. The
normalize_event shim must run BEFORE any requestContext['http'] read.
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from backend.backend.handlers.databases.databaseService import lambda_handler


@pytest.mark.unit
class TestRestApiEventShape:
    """Test REST API (v1) proxy event shape compatibility"""

    def _make_rest_api_event(self, method='GET', path='/database'):
        """Build a REST API (v1) proxy event with flat httpMethod/path"""
        return {
            'httpMethod': method,  # REST v1: flat top-level key
            'path': path,
            'requestContext': {
                'requestId': 'test-request-id',
                'authorizer': {  # REST v1: flat claims dict
                    'claims': {
                        'sub': 'test-user-id',
                        'email': 'test@example.com'
                    }
                }
            },
            'queryStringParameters': {},
            'pathParameters': {},
            'headers': {
                'Authorization': 'Bearer test-token'
            }
        }

    @patch('backend.backend.handlers.databases.databaseService.dynamodb')
    @patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
    def test_database_service_handles_rest_event_shape(self, mock_casbin, mock_dynamodb):
        """Test databaseService.lambda_handler with REST API (v1) event does not KeyError"""
        # Arrange: REST v1 event (flat httpMethod/path)
        event = self._make_rest_api_event(method='GET', path='/database')
        context = MagicMock()

        # Mock DynamoDB table
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        # Mock Casbin to deny API access (we only care that event shape is handled)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = False
        mock_casbin.return_value = mock_enforcer

        # Act: call lambda_handler with REST v1 event
        response = lambda_handler(event, context)

        # Assert: handler returns a dict with statusCode (not KeyError: 'http')
        assert isinstance(response, dict), "Handler must return dict response"
        assert 'statusCode' in response, "Response must have statusCode"
        assert response['statusCode'] == 403, "Enforcer denied, expect 403"

    @patch('backend.backend.handlers.databases.databaseService.dynamodb')
    @patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
    def test_database_service_handles_http_api_event_shape(self, mock_casbin, mock_dynamodb):
        """Test databaseService.lambda_handler with HTTP API (v2) event (already canonical)"""
        # Arrange: HTTP API v2 event (nested requestContext.http)
        event = {
            'requestContext': {
                'http': {
                    'method': 'GET',
                    'path': '/database'
                },
                'authorizer': {
                    'jwt': {
                        'claims': {
                            'sub': 'test-user-id',
                            'email': 'test@example.com'
                        }
                    }
                }
            },
            'queryStringParameters': {},
            'pathParameters': {},
            'headers': {
                'authorization': 'Bearer test-token'
            }
        }
        context = MagicMock()

        # Mock DynamoDB table
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        # Mock Casbin to deny API access
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = False
        mock_casbin.return_value = mock_enforcer

        # Act: call lambda_handler with HTTP API v2 event
        response = lambda_handler(event, context)

        # Assert: handler returns a dict with statusCode
        assert isinstance(response, dict), "Handler must return dict response"
        assert 'statusCode' in response, "Response must have statusCode"
        assert response['statusCode'] == 403, "Enforcer denied, expect 403"
