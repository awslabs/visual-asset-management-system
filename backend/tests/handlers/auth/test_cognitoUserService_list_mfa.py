# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /user/cognito reports mfaEnabled from each user's Cognito MFA settings.

ListUsers carries no MFA information, so the service reads each listed user with AdminGetUser and
reports mfaEnabled as whether its UserMFASettingList is non-empty, the rule the custom auth claims
check applies at sign-in.
"""

import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault('COGNITO_ENABLED', 'true')
os.environ.setdefault('USER_POOL_ID', 'test-pool-id')

from backend.backend.handlers.auth import cognitoUserService as svc  # noqa: E402

_CLAIMS = {"tokens": ["admin-user"], "roles": ["admin"], "mfaEnabled": False}


def _listed_user(username):
    return {
        'Username': username,
        'Attributes': [{'Name': 'email', 'Value': username}],
        'UserStatus': 'CONFIRMED',
        'Enabled': True,
        'UserCreateDate': datetime(2026, 1, 1),
        'UserLastModifiedDate': datetime(2026, 1, 2),
    }


def _list_event():
    return {
        'requestContext': {'http': {'method': 'GET', 'path': '/user/cognito'}},
        'headers': {'authorization': 'Bearer test-token'},
        'queryStringParameters': {},
    }


@pytest.fixture
def cognito():
    """Claims, Casbin and the Cognito client patched so GET /user/cognito reaches the listing."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    client = MagicMock()
    with patch.object(svc, 'request_to_claims', return_value=_CLAIMS), \
         patch.object(svc, 'CasbinEnforcer', return_value=enforcer), \
         patch.object(svc, 'cognito_enabled', True), \
         patch.object(svc, 'cognito_client', client):
        yield client


def _mfa_by_user(response):
    return {user['userId']: user['mfaEnabled'] for user in json.loads(response['body'])['users']}


@pytest.mark.unit
class TestCognitoUserListMfa:
    def test_mfa_enabled_follows_the_mfa_setting_list(self, cognito):
        cognito.list_users.return_value = {
            'Users': [_listed_user('totp@example.com'), _listed_user('plain@example.com')]
        }
        settings = {
            'totp@example.com': {'UserMFASettingList': ['SOFTWARE_TOKEN_MFA']},
            'plain@example.com': {},
        }
        cognito.admin_get_user.side_effect = lambda UserPoolId, Username: settings[Username]

        response = svc.lambda_handler(_list_event(), None)

        assert response['statusCode'] == 200
        assert _mfa_by_user(response) == {'totp@example.com': True, 'plain@example.com': False}
        assert cognito.admin_get_user.call_count == 2

    def test_empty_mfa_setting_list_is_false(self, cognito):
        cognito.list_users.return_value = {'Users': [_listed_user('plain@example.com')]}
        cognito.admin_get_user.return_value = {'UserMFASettingList': []}

        response = svc.lambda_handler(_list_event(), None)

        assert _mfa_by_user(response) == {'plain@example.com': False}

    def test_user_removed_between_list_and_lookup_is_false(self, cognito):
        cognito.list_users.return_value = {'Users': [_listed_user('gone@example.com')]}
        cognito.admin_get_user.side_effect = ClientError(
            {'Error': {'Code': 'UserNotFoundException', 'Message': 'gone'}}, 'AdminGetUser'
        )

        response = svc.lambda_handler(_list_event(), None)

        assert response['statusCode'] == 200
        assert _mfa_by_user(response) == {'gone@example.com': False}

    def test_other_lookup_errors_fail_the_request(self, cognito):
        cognito.list_users.return_value = {'Users': [_listed_user('totp@example.com')]}
        cognito.admin_get_user.side_effect = ClientError(
            {'Error': {'Code': 'TooManyRequestsException', 'Message': 'slow down'}}, 'AdminGetUser'
        )

        response = svc.lambda_handler(_list_event(), None)

        assert response['statusCode'] != 200
        assert 'users' not in json.loads(response['body'])
