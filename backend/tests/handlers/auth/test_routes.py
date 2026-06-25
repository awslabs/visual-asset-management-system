# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the auth routes handler (web route checks + API route listing)."""

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.auth.routes import lambda_handler


def _make_event(method='POST', path='/auth/routes', body=None):
    """Build an API Gateway v2 event for the routes handler."""
    event = {
        'requestContext': {
            'http': {
                'method': method,
                'path': path,
            },
            'authorizer': {
                'jwt': {
                    'claims': {
                        'vams:tokens': json.dumps(['test-user-id']),
                        'vams:roles': json.dumps(['admin']),
                    }
                }
            },
        },
        'headers': {
            'authorization': 'Bearer test-token'
        },
    }
    if body is not None:
        event['body'] = json.dumps(body)
    return event


_CLAIMS = {
    "tokens": ["test-user-id"],
    "roles": ["admin"],
    "externalAttributes": [],
    "mfaEnabled": False,
}


@pytest.mark.unit
class TestCheckWebRoutes:
    """Tests for POST /auth/routes (web route checks)."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_allowed_and_denied_routes(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforce.side_effect = [True, False]
        mock_casbin.return_value = mock_enforcer

        event = _make_event(body={
            'routes': [
                {'method': 'GET', 'route__path': '/assets'},
                {'method': 'GET', 'route__path': '/databases'},
            ]
        })
        response = lambda_handler(event, {})

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        allowed = body['message']['allowedRoutes'] if 'message' in body else body['allowedRoutes']
        assert len(allowed) == 1
        assert allowed[0]['route__path'] == '/assets'
        assert allowed[0]['object__type'] == 'web'

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_missing_body_returns_400(self, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        event = _make_event()  # no body
        response = lambda_handler(event, {})
        assert response['statusCode'] == 400

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_empty_routes_list_returns_400(self, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        event = _make_event(body={'routes': []})
        response = lambda_handler(event, {})
        assert response['statusCode'] == 400

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_invalid_json_body_returns_400(self, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        event = _make_event()
        event['body'] = '{not valid json'
        response = lambda_handler(event, {})
        assert response['statusCode'] == 400

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_no_tokens_returns_403(self, mock_claims):
        mock_claims.return_value = {"tokens": [], "roles": [], "mfaEnabled": False}
        event = _make_event(body={'routes': [{'method': 'GET', 'route__path': '/assets'}]})
        response = lambda_handler(event, {})
        assert response['statusCode'] == 403


@pytest.mark.unit
class TestGetApiRoutes:
    """Tests for GET /auth/routes/api (full API route list)."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_full_list_returned(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True
        mock_casbin.return_value = mock_enforcer

        event = _make_event(method='GET', path='/auth/routes/api')
        response = lambda_handler(event, {})

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        routes = body['message']['routes'] if 'message' in body else body['routes']
        assert len(routes) > 50
        paths = {r['path'] for r in routes}
        assert '/database' in paths
        assert '/auth/routes/api' in paths
        # Internal cross-call routes are excluded from the public listing
        assert '/uploads/{uploadId}/complete/external' not in paths
        # Every route entry carries methods and category
        for r in routes:
            assert r['methods']
            assert r['category']

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_api_authorization_denied_returns_403(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = False
        mock_casbin.return_value = mock_enforcer

        event = _make_event(method='GET', path='/auth/routes/api')
        response = lambda_handler(event, {})
        assert response['statusCode'] == 403


@pytest.mark.unit
class TestGetAllowedApiRoutes:
    """Tests for GET /auth/routes/api/allowed (user-allowed API routes)."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_only_allowed_methods_returned(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True

        # Allow only GET on /database, deny everything else
        def fake_enforce(obj, act):
            return obj.get('route__path') == '/database' and act == 'GET'

        mock_enforcer.enforce.side_effect = fake_enforce
        mock_casbin.return_value = mock_enforcer

        event = _make_event(method='GET', path='/auth/routes/api/allowed')
        response = lambda_handler(event, {})

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        payload = body['message'] if 'message' in body else body
        routes = payload['routes']
        authed = [r for r in routes if r['path'] == '/database']
        assert len(authed) == 1
        assert authed[0]['methods'] == ['GET']
        # Unauthenticated routes are always included
        unauth_paths = {r['path'] for r in routes}
        assert '/api/version' in unauth_paths
        # Routes with no allowed methods are omitted (e.g. /assets had none)
        assert '/assets' not in unauth_paths
        assert payload['userId'] == 'test-user-id'


@pytest.mark.unit
class TestRoutesDispatch:
    """Dispatch-level tests."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_unknown_path_returns_400(self, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        event = _make_event(method='GET', path='/auth/routes/unknown')
        response = lambda_handler(event, {})
        assert response['statusCode'] == 400

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_internal_error_returns_500(self, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        with patch('backend.backend.handlers.auth.routes.CasbinEnforcer', side_effect=Exception("boom")):
            event = _make_event(body={'routes': [{'method': 'GET', 'route__path': '/assets'}]})
            response = lambda_handler(event, {})
        assert response['statusCode'] == 500
