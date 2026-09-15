# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local-mock mode is a local-development affordance and is refused on a deployment.

``USE_LOCAL_MOCKS=true`` makes ``POST /auth/routes`` return every submitted web route as
allowed and skips the empty-token denial, which is what the local development server
(``localDev_api_server.py``) needs to render the frontend without a backend. The same
variable set on a deployed Lambda would turn an authorization decision into an
auto-approval, so the switch is honoured only where ``VAMS_RESOURCE_PARAM_PREFIX`` -- the
SSM prefix a deployed handler resolves its resource names from -- is absent.

Both arms are asserted: the deployment combination enforces Casbin and denies an
unauthenticated caller, and the local combination (no SSM prefix) still auto-approves, so a
correct refusal is distinguishable from local-mock mode being broken outright.
"""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

# The bulk route check flushes its batched denial audit records through get_log_group_name,
# which resolves an env-var override ahead of any SSM lookup. Seeding the audit log-group
# name keeps that resolution offline. Set before the import below, which binds the resolver.
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.handlers.auth import routes as routes_module  # noqa: E402

DEPLOYMENT_PREFIX = "/vams-test-baseStack/resourceNames"

_CLAIMS = {
    "tokens": ["test-user-id"],
    "roles": ["admin"],
    "externalAttributes": [],
    "mfaEnabled": False,
}
_NO_CLAIMS = {"tokens": [], "roles": [], "externalAttributes": [], "mfaEnabled": False}

_ROUTES = [
    {'method': 'GET', 'route__path': '/assets'},
    {'method': 'GET', 'route__path': '/databases'},
]


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


def _allowed_routes(response):
    body = json.loads(response['body'])
    payload = body['message'] if 'message' in body else body
    return payload['allowedRoutes']


@pytest.fixture
def offline_audit_writer():
    """Keep the batched denial flush from reaching CloudWatch."""
    with patch.object(routes_module.auditLogging, '_write_batch_to_cloudwatch',
                      MagicMock(), create=True) as writer:
        yield writer


@pytest.mark.unit
class TestUseLocalMocksResolution:
    """The switch itself: honoured locally, refused where an SSM prefix is present."""

    @pytest.mark.parametrize('value', ['true', 'TRUE'])
    def test_deployment_prefix_refuses_the_switch(self, monkeypatch, value):
        monkeypatch.setenv('USE_LOCAL_MOCKS', value)
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)
        assert routes_module._use_local_mocks() is False

    def test_local_development_still_enables_the_switch(self, monkeypatch):
        """Positive control -- the documented local-dev combination is unchanged."""
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.delenv('VAMS_RESOURCE_PARAM_PREFIX', raising=False)
        assert routes_module._use_local_mocks() is True

    @pytest.mark.parametrize('value', ['false', 'FALSE', '', 'yes', '1'])
    def test_only_the_literal_true_enables_the_switch(self, monkeypatch, value):
        """Positive control -- any other value leaves route checks enforced."""
        monkeypatch.setenv('USE_LOCAL_MOCKS', value)
        monkeypatch.delenv('VAMS_RESOURCE_PARAM_PREFIX', raising=False)
        assert routes_module._use_local_mocks() is False

    def test_unset_switch_on_a_deployment_is_disabled(self, monkeypatch):
        """Positive control -- the shipped configuration."""
        monkeypatch.delenv('USE_LOCAL_MOCKS', raising=False)
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)
        assert routes_module._use_local_mocks() is False

    def test_the_refusal_names_the_variable_in_an_error_log(self, monkeypatch):
        """The combination is recorded, so a misconfigured deployment is diagnosable."""
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)

        with patch.object(routes_module, 'logger') as mock_logger:
            assert routes_module._use_local_mocks() is False

        assert mock_logger.error.call_count == 1
        assert 'USE_LOCAL_MOCKS' in mock_logger.error.call_args[0][0]
        assert 'VAMS_RESOURCE_PARAM_PREFIX' in mock_logger.error.call_args[0][0]

    def test_local_development_is_not_logged_as_an_error(self, monkeypatch):
        """Positive control -- the supported local combination is not flagged."""
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.delenv('VAMS_RESOURCE_PARAM_PREFIX', raising=False)

        with patch.object(routes_module, 'logger') as mock_logger:
            assert routes_module._use_local_mocks() is True

        assert mock_logger.error.call_count == 0


@pytest.mark.unit
class TestCheckWebRoutesOnADeployment:
    """POST /auth/routes with USE_LOCAL_MOCKS set on a deployment."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_empty_tokens_are_still_denied(self, mock_claims, monkeypatch):
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)
        mock_claims.return_value = dict(_NO_CLAIMS)

        response = routes_module.lambda_handler(_make_event(body={'routes': _ROUTES}), {})

        assert response['statusCode'] == 403

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_casbin_is_evaluated_and_denials_are_withheld(
            self, mock_casbin, mock_claims, monkeypatch, offline_audit_writer):
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.service_object.enforce.return_value = False
        mock_casbin.return_value = mock_enforcer

        response = routes_module.lambda_handler(_make_event(body={'routes': _ROUTES}), {})

        assert response['statusCode'] == 200
        assert _allowed_routes(response) == []
        # Every submitted route reached Casbin rather than being auto-approved.
        assert mock_enforcer.service_object.enforce.call_count == len(_ROUTES)


@pytest.mark.unit
class TestCheckWebRoutesLocally:
    """Positive controls: the local-dev path and the ordinary deployment path."""

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    def test_local_mock_mode_auto_approves_every_route(self, mock_claims, monkeypatch):
        monkeypatch.setenv('USE_LOCAL_MOCKS', 'true')
        monkeypatch.delenv('VAMS_RESOURCE_PARAM_PREFIX', raising=False)
        mock_claims.return_value = dict(_NO_CLAIMS)

        response = routes_module.lambda_handler(_make_event(body={'routes': _ROUTES}), {})

        assert response['statusCode'] == 200
        allowed = _allowed_routes(response)
        assert [r['route__path'] for r in allowed] == ['/assets', '/databases']
        assert all(r['object__type'] == 'web' for r in allowed)

    @patch('backend.backend.handlers.auth.routes.request_to_claims')
    @patch('backend.backend.handlers.auth.routes.CasbinEnforcer')
    def test_deployment_without_the_switch_returns_the_allowed_subset(
            self, mock_casbin, mock_claims, monkeypatch, offline_audit_writer):
        monkeypatch.delenv('USE_LOCAL_MOCKS', raising=False)
        monkeypatch.setenv('VAMS_RESOURCE_PARAM_PREFIX', DEPLOYMENT_PREFIX)
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.service_object.enforce.side_effect = [True, False]
        mock_casbin.return_value = mock_enforcer

        response = routes_module.lambda_handler(_make_event(body={'routes': _ROUTES}), {})

        assert response['statusCode'] == 200
        allowed = _allowed_routes(response)
        assert [r['route__path'] for r in allowed] == ['/assets']
