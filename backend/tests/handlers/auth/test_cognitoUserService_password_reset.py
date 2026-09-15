# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cognito password reset route (POST /user/cognito/{userId}/resetPassword).

Password reset must:
  * require ``confirmReset: true`` in the request body - an absent, null, empty or unconfirmed
    body reaches no Cognito admin API at all,
  * leave the account in place: a failure at the mutating call keeps the user, their ``sub``
    and their attributes exactly as they were, and never calls ``admin_delete_user``,
  * report the delivery the user actually receives - a password reset code for an account that
    has signed in before, a new temporary password for one that has not.

Guards FIX-044 (S2-BACKEND-030): a delete-then-recreate password reset destroys the account
permanently when the recreate fails.
"""

import json
import os

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from unittest.mock import MagicMock, patch

os.environ.setdefault('COGNITO_ENABLED', 'true')
os.environ.setdefault('USER_POOL_ID', 'test-pool-id')

from backend.backend.handlers.auth import cognitoUserService as svc  # noqa: E402
from backend.backend.handlers.auth.cognitoUserService import lambda_handler  # noqa: E402

USER_ID = "reset-target@example.com"
RESET_PATH = f"/user/cognito/{USER_ID}/resetPassword"
MOCK_POOL_ID = "us-east-1_testpool"
CONFIRMED_BODY = json.dumps({'confirmReset': True})

_CLAIMS = {"tokens": ["admin-user"], "roles": ["admin"], "mfaEnabled": False}

# Sentinel for "the request carried no body key at all", which is distinct from a null body.
_NO_BODY = object()


def _event(body=_NO_BODY):
    """A v2-shaped password reset request."""
    event = {
        'requestContext': {'http': {'method': 'POST', 'path': RESET_PATH}},
        'pathParameters': {'userId': USER_ID},
        'headers': {'authorization': 'Bearer test-token'},
    }
    if body is not _NO_BODY:
        event['body'] = body
    return event


def _client_error(code, operation='AdminResetUserPassword'):
    return ClientError({'Error': {'Code': code, 'Message': code}}, operation)


def _body(response):
    return json.loads(response['body'])


def _general_error(message):
    """The message a ``VAMSGeneralErrorResponse`` carries on the wire, which is its own text
    behind the ``VAMS General Error:`` prefix that ``models.common`` adds."""
    return str(svc.VAMSGeneralErrorResponse(message))


@pytest.fixture
def auth_allowed():
    """Claims, Casbin and audit logging patched so a request reaches the reset handling."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    with patch.object(svc, 'request_to_claims', return_value=_CLAIMS), \
         patch.object(svc, 'CasbinEnforcer', return_value=enforcer), \
         patch.object(svc, 'log_auth_changes'), \
         patch.object(svc, 'cognito_enabled', True):
        yield


@pytest.fixture
def mock_cognito(auth_allowed):
    """A stub Cognito client, so every admin call the handler makes (or skips) is countable."""
    client = MagicMock()
    with patch.object(svc, 'cognito_client', client), \
         patch.object(svc, 'user_pool_id', MOCK_POOL_ID):
        yield client


@pytest.fixture
def live_cognito(auth_allowed):
    """A moto-backed user pool holding a real user, so "the account is still there" is a
    state assertion rather than an assertion about calls."""
    with mock_aws():
        client = boto3.client('cognito-idp', region_name='us-east-1')
        pool_id = client.create_user_pool(PoolName='vams-reset-test-pool')['UserPool']['Id']
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=USER_ID,
            UserAttributes=[
                {'Name': 'email', 'Value': USER_ID},
                {'Name': 'email_verified', 'Value': 'true'},
            ],
            DesiredDeliveryMediums=['EMAIL'],
        )
        with patch.object(svc, 'cognito_client', client), \
             patch.object(svc, 'user_pool_id', pool_id):
            yield client, pool_id


def _complete_first_sign_in(client, pool_id):
    """Move the account out of FORCE_CHANGE_PASSWORD, as a first sign-in does."""
    client.admin_confirm_sign_up(UserPoolId=pool_id, Username=USER_ID)


def _account_state(client, pool_id):
    """(attributes, userStatus) of the stored account."""
    user = client.admin_get_user(UserPoolId=pool_id, Username=USER_ID)
    return {a['Name']: a['Value'] for a in user['UserAttributes']}, user['UserStatus']


@pytest.mark.unit
class TestConfirmationGuard:
    """An unconfirmed request must not reach Cognito, whatever shape the body arrives in."""

    @pytest.mark.parametrize("body,label", [
        (_NO_BODY, "no body key at all"),
        (None, "body key present but null"),
        ('null', "body is the JSON literal null"),
        ('', "body is an empty string"),
        ('   ', "body is whitespace"),
        ('{}', "body is an empty JSON object"),
        (json.dumps({'confirmReset': False}), "confirmReset explicitly false"),
        (json.dumps({'userId': USER_ID}), "body carries no confirmReset field"),
        ({'userId': USER_ID}, "already-parsed dict with no confirmReset field"),
    ])
    def test_unconfirmed_request_reaches_no_cognito_api(self, mock_cognito, body, label):
        response = lambda_handler(_event(body), {})

        assert response['statusCode'] == 400, label
        assert 'confirmReset' in _body(response)['message'], label
        assert mock_cognito.method_calls == [], f"{label} still called Cognito"

    @pytest.mark.parametrize("body,label", [
        ('[1, 2]', "body is a JSON array"),
        ('"confirmReset"', "body is a bare JSON string"),
    ])
    def test_non_object_body_reaches_no_cognito_api(self, mock_cognito, body, label):
        response = lambda_handler(_event(body), {})

        assert response['statusCode'] == 400, label
        assert mock_cognito.method_calls == [], f"{label} still called Cognito"

    def test_malformed_json_body_reaches_no_cognito_api(self, mock_cognito):
        response = lambda_handler(_event('{not json'), {})

        assert response['statusCode'] == 400
        assert mock_cognito.method_calls == []


@pytest.mark.unit
class TestConfirmedReset:
    """A confirmed reset starts the Cognito reset flow and never deletes the account."""

    def test_confirmed_reset_starts_the_reset_flow(self, mock_cognito):
        """Positive control: an ordinary confirmed reset succeeds, so a guard that refuses
        every request cannot pass this file."""
        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        body = _body(response)
        assert body['success'] is True
        assert body['operation'] == 'resetPassword'
        assert 'reset code' in body['message']
        mock_cognito.admin_reset_user_password.assert_called_once_with(
            UserPoolId=MOCK_POOL_ID, Username=USER_ID)
        mock_cognito.admin_delete_user.assert_not_called()
        mock_cognito.admin_create_user.assert_not_called()

    def test_account_without_a_password_gets_the_invitation_resent(self, mock_cognito):
        mock_cognito.admin_reset_user_password.side_effect = _client_error('NotAuthorizedException')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        assert 'temporary password' in _body(response)['message']
        mock_cognito.admin_create_user.assert_called_once_with(
            UserPoolId=MOCK_POOL_ID,
            Username=USER_ID,
            MessageAction='RESEND',
            DesiredDeliveryMediums=['EMAIL'],
        )
        mock_cognito.admin_delete_user.assert_not_called()

    def test_reset_failure_returns_a_generic_error_and_deletes_nothing(self, mock_cognito):
        mock_cognito.admin_reset_user_password.side_effect = _client_error('InternalErrorException')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        assert _body(response)['message'] == _general_error('Error resetting password')
        mock_cognito.admin_delete_user.assert_not_called()
        mock_cognito.admin_create_user.assert_not_called()

    def test_invitation_resend_failure_deletes_nothing(self, mock_cognito):
        mock_cognito.admin_reset_user_password.side_effect = _client_error('NotAuthorizedException')
        mock_cognito.admin_create_user.side_effect = _client_error(
            'UnsupportedUserStateException', 'AdminCreateUser')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        mock_cognito.admin_delete_user.assert_not_called()

    def test_missing_user_is_reported_without_a_delete(self, mock_cognito):
        mock_cognito.admin_reset_user_password.side_effect = _client_error('UserNotFoundException')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        assert _body(response)['message'] == _general_error('User not found')
        mock_cognito.admin_delete_user.assert_not_called()
        mock_cognito.admin_create_user.assert_not_called()


@pytest.mark.unit
class TestAccountSurvivesTheReset:
    """State assertions against a moto-backed pool: the account outlives both success and
    failure of the reset."""

    def test_signed_in_account_keeps_its_identity_and_gets_a_reset_code(self, live_cognito):
        client, pool_id = live_cognito
        _complete_first_sign_in(client, pool_id)
        before, _ = _account_state(client, pool_id)

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        assert 'reset code' in _body(response)['message']
        after, status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert after['email'] == before['email']
        # RESET_REQUIRED is the state that makes Cognito send the reset code and challenge the
        # user for it at sign-in, which is what the response message promises
        assert status == 'RESET_REQUIRED'

    def test_failure_at_the_mutating_call_leaves_the_account_intact(self, live_cognito):
        """The reset call is the single mutating step of the operation. A failure there must
        cost nothing: same user, same sub, same attributes, same status."""
        client, pool_id = live_cognito
        _complete_first_sign_in(client, pool_id)
        before, before_status = _account_state(client, pool_id)

        with patch.object(client, 'admin_reset_user_password',
                          side_effect=_client_error('InternalErrorException')):
            response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        after, after_status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert after['email'] == before['email']
        assert after_status == before_status

    def test_account_without_a_first_sign_in_survives_and_is_re_invited(self, live_cognito):
        """The freshly created user is in FORCE_CHANGE_PASSWORD, the state the reset code flow
        cannot serve, so the invitation is resent with a new temporary password."""
        client, pool_id = live_cognito

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        assert 'temporary password' in _body(response)['message']
        # moto rebuilds its stored user record for a RESEND, so existence and status are what
        # can be asserted here; real Cognito keeps the same record and sub
        _, status = _account_state(client, pool_id)
        assert status == 'FORCE_CHANGE_PASSWORD'
