# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cognito password reset route (POST /user/cognito/{userId}/resetPassword).

Password reset must:
  * require ``confirmReset: true`` in the request body - an absent, null, empty or unconfirmed
    body reaches no Cognito admin API at all,
  * give the user a new temporary password by email, whatever state the account is in: the
    current password is replaced with a random one nobody holds (``admin_set_user_password``,
    ``Permanent=False``, so the account is in FORCE_CHANGE_PASSWORD), then the invitation is
    resent (``admin_create_user`` with ``MessageAction='RESEND'``), which has Cognito generate the
    temporary password and email it with the invitation template,
  * never start Cognito's reset-code flow (``admin_reset_user_password``), which emails a
    verification code instead of a temporary password,
  * leave the account in place: a failure at either call keeps the user and their attributes,
    and ``admin_delete_user`` is never called.

Guards FIX-044 (S2-BACKEND-030): a delete-then-recreate password reset destroys the account
permanently when the recreate fails. Guards issue #575: the reset emailed a verification code.
"""

import json
import os
import string

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from unittest.mock import MagicMock, call, patch

os.environ.setdefault('COGNITO_ENABLED', 'true')
os.environ.setdefault('USER_POOL_ID', 'test-pool-id')

from backend.backend.handlers.auth import cognitoUserService as svc  # noqa: E402
from backend.backend.handlers.auth.cognitoUserService import lambda_handler  # noqa: E402

USER_ID = "reset-target@example.com"
RESET_PATH = f"/user/cognito/{USER_ID}/resetPassword"
MOCK_POOL_ID = "us-east-1_testpool"
CONFIRMED_BODY = json.dumps({'confirmReset': True})
RESEND_CALL = call(
    UserPoolId=MOCK_POOL_ID,
    Username=USER_ID,
    MessageAction='RESEND',
    DesiredDeliveryMediums=['EMAIL'],
)

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


def _client_error(code, operation='AdminSetUserPassword'):
    return ClientError({'Error': {'Code': code, 'Message': code}}, operation)


def _body(response):
    return json.loads(response['body'])


def _general_error(message):
    """The message a ``VAMSGeneralErrorResponse`` carries on the wire, which is its own text
    behind the ``VAMS General Error:`` prefix that ``models.common`` adds."""
    return str(svc.VAMSGeneralErrorResponse(message))


def _set_password_kwargs(client):
    """The keyword arguments of the single ``admin_set_user_password`` call."""
    client.admin_set_user_password.assert_called_once()
    return client.admin_set_user_password.call_args.kwargs


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
    state assertion rather than an assertion about calls.

    The invitation resend is recorded instead of run through moto: moto rebuilds its stored
    user record for a RESEND (new sub, attributes dropped), which real Cognito does not do.
    On a real user pool the resend keeps the sub, the email and its verification, and leaves the
    account in FORCE_CHANGE_PASSWORD.
    """
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
        resend = MagicMock(name='admin_create_user')
        with patch.object(svc, 'cognito_client', client), \
             patch.object(svc, 'user_pool_id', pool_id), \
             patch.object(client, 'admin_create_user', resend):
            yield client, pool_id, resend


def _complete_first_sign_in(client, pool_id):
    """Move the account to CONFIRMED with a password of the user's own, as a first sign-in does."""
    client.admin_set_user_password(
        UserPoolId=pool_id, Username=USER_ID, Password='Own-passw0rd!', Permanent=True)


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
    """A confirmed reset replaces the password, then resends the invitation with a new
    temporary password; it never starts the reset-code flow and never deletes the account."""

    def test_confirmed_reset_sends_a_temporary_password(self, mock_cognito):
        """Positive control: an ordinary confirmed reset succeeds, so a guard that refuses
        every request cannot pass this file."""
        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        body = _body(response)
        assert body['success'] is True
        assert body['operation'] == 'resetPassword'
        assert 'temporary password' in body['message']
        assert 'code' not in body['message']

        kwargs = _set_password_kwargs(mock_cognito)
        assert kwargs['UserPoolId'] == MOCK_POOL_ID
        assert kwargs['Username'] == USER_ID
        assert kwargs['Permanent'] is False
        assert mock_cognito.admin_create_user.call_args_list == [RESEND_CALL]
        # the password is replaced first: RESEND is refused for an account that is not in
        # FORCE_CHANGE_PASSWORD
        names = [c[0] for c in mock_cognito.method_calls]
        assert names == ['admin_set_user_password', 'admin_create_user']

    def test_reset_never_uses_the_reset_code_flow(self, mock_cognito):
        lambda_handler(_event(CONFIRMED_BODY), {})

        mock_cognito.admin_reset_user_password.assert_not_called()
        mock_cognito.admin_delete_user.assert_not_called()

    def test_the_replaced_password_is_random_and_never_returned(self, mock_cognito):
        first = lambda_handler(_event(CONFIRMED_BODY), {})
        first_password = _set_password_kwargs(mock_cognito)['Password']
        mock_cognito.reset_mock()
        lambda_handler(_event(CONFIRMED_BODY), {})
        second_password = _set_password_kwargs(mock_cognito)['Password']

        assert first_password != second_password
        assert first_password not in first['body']
        # the temporary password is Cognito's, generated on the resend; VAMS supplies none
        assert 'TemporaryPassword' not in mock_cognito.admin_create_user.call_args.kwargs

    def test_set_password_failure_returns_a_generic_error_and_sends_nothing(self, mock_cognito):
        mock_cognito.admin_set_user_password.side_effect = _client_error('InternalErrorException')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        assert _body(response)['message'] == _general_error('Error resetting password')
        mock_cognito.admin_create_user.assert_not_called()
        mock_cognito.admin_delete_user.assert_not_called()

    def test_missing_user_is_reported_without_a_resend(self, mock_cognito):
        mock_cognito.admin_set_user_password.side_effect = _client_error('UserNotFoundException')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        assert _body(response)['message'] == _general_error('User not found')
        mock_cognito.admin_create_user.assert_not_called()
        mock_cognito.admin_delete_user.assert_not_called()

    def test_resend_failure_says_the_email_was_not_sent_and_deletes_nothing(self, mock_cognito):
        mock_cognito.admin_create_user.side_effect = _client_error(
            'InvalidParameterException', 'AdminCreateUser')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        message = _body(response)['message']
        assert 'could not be sent' in message
        assert 'again' in message
        mock_cognito.admin_delete_user.assert_not_called()


@pytest.mark.unit
class TestUnusablePassword:
    """The password set before the resend must pass the user pool password policy."""

    @pytest.mark.parametrize("attempt", range(50))
    def test_meets_the_pool_policy(self, attempt):
        password = svc.generate_unusable_password()

        assert len(password) == svc.UNUSABLE_PASSWORD_LENGTH
        assert len(password) <= 256
        assert any(c in string.ascii_lowercase for c in password)
        assert any(c in string.ascii_uppercase for c in password)
        assert any(c in string.digits for c in password)
        assert any(c in svc.UNUSABLE_PASSWORD_SYMBOLS for c in password)
        assert not any(c.isspace() for c in password)

    def test_is_different_every_time(self):
        assert len({svc.generate_unusable_password() for _ in range(100)}) == 100


@pytest.mark.unit
class TestAccountSurvivesTheReset:
    """State assertions against a moto-backed pool: the account outlives both success and
    failure of the reset, and ends in FORCE_CHANGE_PASSWORD from every starting state."""

    def test_signed_in_account_keeps_its_identity_and_must_change_its_password(self, live_cognito):
        client, pool_id, resend = live_cognito
        _complete_first_sign_in(client, pool_id)
        before, before_status = _account_state(client, pool_id)
        assert before_status == 'CONFIRMED'

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        assert 'temporary password' in _body(response)['message']
        after, status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert after['email'] == before['email']
        # FORCE_CHANGE_PASSWORD is the state in which Cognito accepts the RESEND, emails the
        # temporary password, and makes the user choose a new password at sign-in
        assert status == 'FORCE_CHANGE_PASSWORD'
        resend.assert_called_once_with(
            UserPoolId=pool_id, Username=USER_ID, MessageAction='RESEND',
            DesiredDeliveryMediums=['EMAIL'])

    def test_account_left_waiting_for_a_reset_code_is_recovered(self, live_cognito):
        """An account a previous reset left in RESET_REQUIRED gets a temporary password too."""
        client, pool_id, resend = live_cognito
        _complete_first_sign_in(client, pool_id)
        client.admin_reset_user_password(UserPoolId=pool_id, Username=USER_ID)
        assert _account_state(client, pool_id)[1] == 'RESET_REQUIRED'

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        assert _account_state(client, pool_id)[1] == 'FORCE_CHANGE_PASSWORD'
        resend.assert_called_once()

    def test_account_without_a_first_sign_in_is_sent_a_new_temporary_password(self, live_cognito):
        client, pool_id, resend = live_cognito
        before, before_status = _account_state(client, pool_id)
        assert before_status == 'FORCE_CHANGE_PASSWORD'

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 200
        after, status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert status == 'FORCE_CHANGE_PASSWORD'
        resend.assert_called_once()

    def test_failure_at_the_password_change_leaves_the_account_intact(self, live_cognito):
        """A failure at the first mutating call must cost nothing: same user, same sub, same
        attributes, same status, and no invitation."""
        client, pool_id, resend = live_cognito
        _complete_first_sign_in(client, pool_id)
        before, before_status = _account_state(client, pool_id)

        with patch.object(client, 'admin_set_user_password',
                          side_effect=_client_error('InternalErrorException')):
            response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        after, after_status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert after['email'] == before['email']
        assert after_status == before_status
        resend.assert_not_called()

    def test_failure_at_the_resend_keeps_the_account(self, live_cognito):
        client, pool_id, resend = live_cognito
        _complete_first_sign_in(client, pool_id)
        before, _ = _account_state(client, pool_id)
        resend.side_effect = _client_error('InvalidParameterException', 'AdminCreateUser')

        response = lambda_handler(_event(CONFIRMED_BODY), {})

        assert response['statusCode'] == 400
        after, status = _account_state(client, pool_id)
        assert after['sub'] == before['sub']
        assert after['email'] == before['email']
        # a second reset can resend from this state
        assert status == 'FORCE_CHANGE_PASSWORD'
