# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the user-level (self-service) API key routes (/auth/user/api-keys).

User scope must:
  * restrict list/get/update/delete to keys owned by the requesting user,
  * always tie created keys to the requesting user (ignore any userId input),
  * require an expiration date on create,
  * cap expiration at USER_API_KEY_MAX_EXPIRATION_DAYS from key creation,
    including on later edits (measured from ORIGINAL creation),
  * leave the admin routes (/auth/api-keys) behavior unchanged
    (backwards compatibility).
"""

import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault('API_KEY_STORAGE_TABLE_NAME', 'test-api-key-table')
os.environ.setdefault('USER_ROLES_STORAGE_TABLE_NAME', 'test-user-roles-table')

from backend.backend.handlers.auth import apiKeyService
from backend.backend.handlers.auth.apiKeyService import lambda_handler

USER = "self-user"
OTHER_USER = "other-user"
KEY_ID_OWN = "11111111-1111-4111-8111-111111111111"
KEY_ID_OTHER = "22222222-2222-4222-8222-222222222222"


def _iso(dt):
    """ISO 8601 with seconds precision (client-style, fits the model's max_length)."""
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _now():
    return datetime.now(timezone.utc)


def _make_event(method, path, body=None, api_key_id=None):
    event = {
        'requestContext': {
            'http': {'method': method, 'path': path},
            'authorizer': {'jwt': {'claims': {
                'vams:tokens': json.dumps([USER]),
                'vams:roles': json.dumps(['someRole']),
            }}},
        },
        'headers': {'authorization': 'Bearer test-token'},
    }
    if api_key_id:
        event['pathParameters'] = {'apiKeyId': api_key_id}
    if body is not None:
        event['body'] = json.dumps(body)
    return event


def _own_key_item(created_at=None, expires_at=None):
    created = created_at or _iso(_now() - timedelta(days=10))
    return {
        'apiKeyId': KEY_ID_OWN,
        'apiKeyName': 'own-key',
        'apiKeyHash': 'hash',
        'description': 'mine',
        'userId': USER,
        'createdBy': USER,
        'createdAt': created,
        'updatedAt': created,
        'expiresAt': expires_at or _iso(_now() + timedelta(days=30)),
        'isActive': 'true',
    }


def _other_key_item():
    created = _iso(_now() - timedelta(days=5))
    return {
        'apiKeyId': KEY_ID_OTHER,
        'apiKeyName': 'other-key',
        'apiKeyHash': 'hash2',
        'description': 'not mine',
        'userId': OTHER_USER,
        'createdBy': 'admin',
        'createdAt': created,
        'updatedAt': created,
        'expiresAt': '',
        'isActive': 'true',
    }


def _real_to_update_expr(record, op="SET"):
    """Real to_update_expr logic (the handler may have bound a mock at import time)."""
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v) for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


@pytest.fixture
def mock_env(monkeypatch):
    """Patch claims, Casbin, audit logging, and the DynamoDB tables."""
    claims = {"tokens": [USER], "roles": ["someRole"], "mfaEnabled": False}
    table = MagicMock()
    roles_table = MagicMock()
    roles_table.query.return_value = {'Items': [{'userId': USER, 'roleName': 'someRole'}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True

    with patch.object(apiKeyService, 'request_to_claims', return_value=claims), \
         patch.object(apiKeyService, 'CasbinEnforcer', return_value=enforcer), \
         patch.object(apiKeyService, 'log_auth_changes'), \
         patch.object(apiKeyService, 'to_update_expr', _real_to_update_expr), \
         patch.object(apiKeyService, 'api_key_table', table), \
         patch.object(apiKeyService, 'user_roles_table', roles_table):
        yield {'table': table, 'roles_table': roles_table}


@pytest.mark.unit
class TestUserScopeList:
    def test_list_filters_to_own_keys(self, mock_env):
        mock_env['table'].scan.return_value = {'Items': [_own_key_item(), _other_key_item()]}
        response = lambda_handler(_make_event('GET', '/auth/user/api-keys'), {})
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        items = body['message']['Items'] if 'message' in body else body['Items']
        assert len(items) == 1
        assert items[0]['apiKeyId'] == KEY_ID_OWN
        assert 'apiKeyHash' not in items[0]

    def test_admin_list_unchanged_returns_all(self, mock_env):
        """Backwards compatibility: the admin route still returns every key."""
        mock_env['table'].scan.return_value = {'Items': [_own_key_item(), _other_key_item()]}
        response = lambda_handler(_make_event('GET', '/auth/api-keys'), {})
        body = json.loads(response['body'])
        items = body['message']['Items'] if 'message' in body else body['Items']
        assert len(items) == 2


@pytest.mark.unit
class TestUserScopeGet:
    def test_get_own_key(self, mock_env):
        mock_env['table'].get_item.return_value = {'Item': _own_key_item()}
        response = lambda_handler(
            _make_event('GET', f'/auth/user/api-keys/{KEY_ID_OWN}', api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 200

    def test_get_other_users_key_hidden(self, mock_env):
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        response = lambda_handler(
            _make_event('GET', f'/auth/user/api-keys/{KEY_ID_OTHER}', api_key_id=KEY_ID_OTHER), {})
        # Other users' keys must look like they don't exist
        assert response['statusCode'] == 400
        assert 'not found' in json.loads(response['body'])['message'].lower()

    def test_admin_get_other_users_key_unchanged(self, mock_env):
        """Backwards compatibility: admins can still fetch any key."""
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        response = lambda_handler(
            _make_event('GET', f'/auth/api-keys/{KEY_ID_OTHER}', api_key_id=KEY_ID_OTHER), {})
        assert response['statusCode'] == 200


@pytest.mark.unit
class TestUserScopeCreate:
    def _create_body(self, expires_days=30, **overrides):
        body = {
            'apiKeyName': 'my-new-key',
            'description': 'self-service key',
            'expiresAt': _iso(_now() + timedelta(days=expires_days)),
        }
        body.update(overrides)
        return body

    def test_create_ties_key_to_requesting_user(self, mock_env):
        mock_env['table'].scan.return_value = {'Items': []}
        response = lambda_handler(
            _make_event('POST', '/auth/user/api-keys', body=self._create_body()), {})
        assert response['statusCode'] == 200
        put_item = mock_env['table'].put_item.call_args[1]['Item']
        assert put_item['userId'] == USER
        assert put_item['createdBy'] == USER
        body = json.loads(response['body'])
        payload = body['message'] if 'message' in body else body
        assert payload['apiKey'].startswith('vams_')
        assert 'apiKeyHash' not in payload

    def test_create_ignores_supplied_user_id(self, mock_env):
        """A user cannot create a key for someone else via the user route."""
        mock_env['table'].scan.return_value = {'Items': []}
        response = lambda_handler(
            _make_event('POST', '/auth/user/api-keys',
                        body=self._create_body(userId=OTHER_USER)), {})
        assert response['statusCode'] == 200
        put_item = mock_env['table'].put_item.call_args[1]['Item']
        assert put_item['userId'] == USER

    def test_create_requires_expiration(self, mock_env):
        body = {'apiKeyName': 'k', 'description': 'd'}  # no expiresAt
        response = lambda_handler(_make_event('POST', '/auth/user/api-keys', body=body), {})
        assert response['statusCode'] == 400

    def test_create_rejects_expiration_beyond_365_days(self, mock_env):
        mock_env['table'].scan.return_value = {'Items': []}
        response = lambda_handler(
            _make_event('POST', '/auth/user/api-keys', body=self._create_body(expires_days=400)), {})
        assert response['statusCode'] == 400
        assert '365' in json.loads(response['body'])['message']

    def test_create_accepts_expiration_at_boundary(self, mock_env):
        mock_env['table'].scan.return_value = {'Items': []}
        response = lambda_handler(
            _make_event('POST', '/auth/user/api-keys', body=self._create_body(expires_days=364)), {})
        assert response['statusCode'] == 200

    def test_create_rejects_past_expiration(self, mock_env):
        mock_env['table'].scan.return_value = {'Items': []}
        response = lambda_handler(
            _make_event('POST', '/auth/user/api-keys', body=self._create_body(expires_days=-1)), {})
        assert response['statusCode'] == 400

    def test_admin_create_without_expiration_unchanged(self, mock_env):
        """Backwards compatibility: the admin route still allows no expiration
        and creating keys for any user."""
        mock_env['table'].scan.return_value = {'Items': []}
        body = {'apiKeyName': 'admin-key', 'userId': OTHER_USER, 'description': 'd'}
        response = lambda_handler(_make_event('POST', '/auth/api-keys', body=body), {})
        assert response['statusCode'] == 200
        put_item = mock_env['table'].put_item.call_args[1]['Item']
        assert put_item['userId'] == OTHER_USER
        assert put_item['expiresAt'] == ''


@pytest.mark.unit
class TestUserScopeUpdate:
    def test_update_own_key_within_window(self, mock_env):
        item = _own_key_item(created_at=_iso(_now() - timedelta(days=10)))
        mock_env['table'].get_item.return_value = {'Item': item}
        body = {'expiresAt': _iso(_now() + timedelta(days=100))}
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OWN}', body=body,
                        api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 200

    def test_update_cannot_exceed_365_days_from_original_creation(self, mock_env):
        # Key created 300 days ago: max extension is only ~65 more days
        item = _own_key_item(created_at=_iso(_now() - timedelta(days=300)))
        mock_env['table'].get_item.return_value = {'Item': item}
        body = {'expiresAt': _iso(_now() + timedelta(days=100))}  # 400 days from creation
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OWN}', body=body,
                        api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 400
        assert '365' in json.loads(response['body'])['message']

    def test_update_within_window_of_old_key(self, mock_env):
        item = _own_key_item(created_at=_iso(_now() - timedelta(days=300)))
        mock_env['table'].get_item.return_value = {'Item': item}
        body = {'expiresAt': _iso(_now() + timedelta(days=60))}  # 360 days from creation
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OWN}', body=body,
                        api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 200

    def test_update_cannot_clear_expiration(self, mock_env):
        item = _own_key_item()
        mock_env['table'].get_item.return_value = {'Item': item}
        body = {'expiresAt': ''}
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OWN}', body=body,
                        api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 400

    def test_update_other_users_key_hidden(self, mock_env):
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        body = {'description': 'hijack'}
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OTHER}', body=body,
                        api_key_id=KEY_ID_OTHER), {})
        assert response['statusCode'] == 400
        assert 'not found' in json.loads(response['body'])['message'].lower()
        mock_env['table'].update_item.assert_not_called()

    def test_update_description_only_no_expiration_check(self, mock_env):
        # Editing only the description of an old key must not trip the window check
        item = _own_key_item(created_at=_iso(_now() - timedelta(days=400)))
        mock_env['table'].get_item.return_value = {'Item': item}
        body = {'description': 'new description'}
        response = lambda_handler(
            _make_event('PUT', f'/auth/user/api-keys/{KEY_ID_OWN}', body=body,
                        api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 200

    def test_admin_update_clear_expiration_unchanged(self, mock_env):
        """Backwards compatibility: the admin route can still clear expiration
        on any user's key."""
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        body = {'expiresAt': ''}
        response = lambda_handler(
            _make_event('PUT', f'/auth/api-keys/{KEY_ID_OTHER}', body=body,
                        api_key_id=KEY_ID_OTHER), {})
        assert response['statusCode'] == 200


@pytest.mark.unit
class TestUserScopeDelete:
    def test_delete_own_key(self, mock_env):
        mock_env['table'].get_item.return_value = {'Item': _own_key_item()}
        response = lambda_handler(
            _make_event('DELETE', f'/auth/user/api-keys/{KEY_ID_OWN}', api_key_id=KEY_ID_OWN), {})
        assert response['statusCode'] == 200
        mock_env['table'].delete_item.assert_called_once()

    def test_delete_other_users_key_hidden(self, mock_env):
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        response = lambda_handler(
            _make_event('DELETE', f'/auth/user/api-keys/{KEY_ID_OTHER}', api_key_id=KEY_ID_OTHER), {})
        assert response['statusCode'] == 400
        mock_env['table'].delete_item.assert_not_called()

    def test_admin_delete_any_key_unchanged(self, mock_env):
        """Backwards compatibility: admins can still delete any user's key."""
        mock_env['table'].get_item.return_value = {'Item': _other_key_item()}
        response = lambda_handler(
            _make_event('DELETE', f'/auth/api-keys/{KEY_ID_OTHER}', api_key_id=KEY_ID_OTHER), {})
        assert response['statusCode'] == 200
        mock_env['table'].delete_item.assert_called_once()
