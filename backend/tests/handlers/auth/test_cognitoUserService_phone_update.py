# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cognito user update route (PUT /user/cognito/{userId}).

The update is partial, so:
  * an attribute the request does not mention keeps the value it has - an email-only update
    reaches Cognito with no ``phone_number`` attribute at all,
  * removing the stored number takes the explicit ``clearPhone`` instruction, which works on
    its own and alongside an email change,
  * a contradictory or non-boolean ``clearPhone`` is refused before any Cognito call.

Guards S2-BACKEND-092: an email-only update wrote ``phone_number=''`` and
``phone_number_verified='false'``, destroying the stored number on a routine edit.
"""

import json
import os

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

os.environ.setdefault('COGNITO_ENABLED', 'true')
os.environ.setdefault('USER_POOL_ID', 'test-pool-id')

from backend.backend.handlers.auth import cognitoUserService as svc  # noqa: E402
from backend.backend.handlers.auth.cognitoUserService import lambda_handler  # noqa: E402

USER_ID = "update-target@example.com"
UPDATE_PATH = f"/user/cognito/{USER_ID}"
MOCK_POOL_ID = "us-east-1_testpool"
STORED_PHONE = "+15551230000"
NEW_EMAIL = "update-target@new.example.com"

_CLAIMS = {"tokens": ["admin-user"], "roles": ["admin"], "mfaEnabled": False}


def _event(body):
    """A v2-shaped user update request."""
    return {
        'requestContext': {'http': {'method': 'PUT', 'path': UPDATE_PATH}},
        'pathParameters': {'userId': USER_ID},
        'headers': {'authorization': 'Bearer test-token'},
        'body': json.dumps(body) if isinstance(body, (dict, list)) else body,
    }


def _body(response):
    return json.loads(response['body'])


def _sent_attributes(client):
    """The {Name: Value} map of the single admin_update_user_attributes call."""
    client.admin_update_user_attributes.assert_called_once()
    kwargs = client.admin_update_user_attributes.call_args.kwargs
    return {attr['Name']: attr['Value'] for attr in kwargs['UserAttributes']}


@pytest.fixture
def auth_allowed():
    """Claims and Casbin patched so a request reaches the update handling."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    with patch.object(svc, 'request_to_claims', return_value=_CLAIMS), \
         patch.object(svc, 'CasbinEnforcer', return_value=enforcer), \
         patch.object(svc, 'cognito_enabled', True):
        yield


@pytest.fixture
def audit_log(auth_allowed):
    """The audit writer, so the recorded update instruction is assertable."""
    with patch.object(svc, 'log_auth_changes') as writer:
        yield writer


@pytest.fixture
def mock_cognito(audit_log):
    """A stub Cognito client, so every attribute the handler sends (or omits) is countable."""
    client = MagicMock()
    with patch.object(svc, 'cognito_client', client), \
         patch.object(svc, 'user_pool_id', MOCK_POOL_ID):
        yield client


@pytest.fixture
def live_cognito(audit_log):
    """A moto-backed pool holding a user with a phone number, so "the number survived" is a
    state assertion rather than an assertion about calls."""
    with mock_aws():
        client = boto3.client('cognito-idp', region_name='us-east-1')
        pool_id = client.create_user_pool(PoolName='vams-update-test-pool')['UserPool']['Id']
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=USER_ID,
            UserAttributes=[
                {'Name': 'email', 'Value': USER_ID},
                {'Name': 'email_verified', 'Value': 'true'},
                {'Name': 'phone_number', 'Value': STORED_PHONE},
                {'Name': 'phone_number_verified', 'Value': 'true'},
            ],
            DesiredDeliveryMediums=['EMAIL'],
        )
        with patch.object(svc, 'cognito_client', client), \
             patch.object(svc, 'user_pool_id', pool_id):
            yield client, pool_id


def _stored_attributes(client, pool_id):
    user = client.admin_get_user(UserPoolId=pool_id, Username=USER_ID)
    return {a['Name']: a['Value'] for a in user['UserAttributes']}


@pytest.mark.unit
class TestOmittedPhoneIsLeftAlone:
    """A request that does not mention the phone must not touch the phone attributes."""

    def test_email_only_update_sends_no_phone_attribute(self, mock_cognito):
        response = lambda_handler(_event({'email': NEW_EMAIL}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert 'phone_number' not in attributes
        assert 'phone_number_verified' not in attributes

    def test_email_only_update_keeps_the_stored_number(self, live_cognito):
        """State assertion: the number the pool holds is the number it held before."""
        client, pool_id = live_cognito
        before = _stored_attributes(client, pool_id)
        # Precondition, so a fixture that never stored the number cannot make this pass
        assert before['phone_number'] == STORED_PHONE

        response = lambda_handler(_event({'email': NEW_EMAIL}), {})

        assert response['statusCode'] == 200
        after = _stored_attributes(client, pool_id)
        assert after['phone_number'] == STORED_PHONE
        assert after['phone_number_verified'] == 'true'
        assert after['email'] == NEW_EMAIL

    def test_email_only_update_still_updates_the_email(self, mock_cognito):
        """Positive control: the email half of the update is unaffected."""
        response = lambda_handler(_event({'email': NEW_EMAIL}), {})

        assert response['statusCode'] == 200
        assert _body(response)['operation'] == 'update'
        attributes = _sent_attributes(mock_cognito)
        assert attributes['email'] == NEW_EMAIL
        assert attributes['email_verified'] == 'true'

    def test_phone_update_still_replaces_the_number(self, mock_cognito):
        """Positive control: a supplied phone number is still written and marked verified."""
        response = lambda_handler(_event({'phone': '+15559998888'}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert attributes['phone_number'] == '+15559998888'
        assert attributes['phone_number_verified'] == 'true'

    def test_empty_update_is_still_refused(self, mock_cognito):
        """Positive control: the model's at-least-one-field rule still holds, so the
        clearPhone handling opened no path for an instruction-free update."""
        response = lambda_handler(_event({}), {})

        assert response['statusCode'] == 400
        mock_cognito.admin_update_user_attributes.assert_not_called()


@pytest.mark.unit
class TestClearPhoneSentinel:
    """clearPhone is the only way to remove the stored number."""

    @pytest.mark.parametrize("sentinel", [True, "true", "True"])
    def test_clear_phone_alongside_an_email_clears_the_number(self, mock_cognito, audit_log,
                                                              sentinel):
        """The JSON boolean is the form a client sends, so it is the form that has to work: the
        validate() dispatcher refuses a non-string value before any rule runs."""
        response = lambda_handler(_event({'email': NEW_EMAIL, 'clearPhone': sentinel}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert attributes['email'] == NEW_EMAIL
        assert attributes['phone_number'] == ''
        assert attributes['phone_number_verified'] == 'false'
        assert audit_log.call_args.args[2]['clearPhone'] is True

    def test_clear_phone_on_its_own_clears_the_number(self, mock_cognito):
        """The update model carries email and phone only, so a removal-only request has to be
        accepted without either of them."""
        response = lambda_handler(_event({'clearPhone': True}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert attributes['phone_number'] == ''
        assert attributes['phone_number_verified'] == 'false'
        assert 'email' not in attributes

    def test_clear_phone_on_its_own_clears_the_stored_number(self, live_cognito):
        """State assertion: the removal reaches the pool."""
        client, pool_id = live_cognito
        assert _stored_attributes(client, pool_id)['phone_number'] == STORED_PHONE

        response = lambda_handler(_event({'clearPhone': True}), {})

        assert response['statusCode'] == 200
        after = _stored_attributes(client, pool_id)
        assert after.get('phone_number', '') == ''
        assert after['phone_number_verified'] == 'false'

    @pytest.mark.parametrize("sentinel", [False, "false"])
    def test_clear_phone_false_leaves_the_number_alone(self, mock_cognito, sentinel):
        response = lambda_handler(_event({'email': NEW_EMAIL, 'clearPhone': sentinel}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert 'phone_number' not in attributes
        assert 'phone_number_verified' not in attributes

    def test_a_null_phone_alongside_clear_phone_still_clears(self, mock_cognito):
        """A client that sends the phone field as null rather than omitting it is still asking
        for the removal, not contradicting it."""
        response = lambda_handler(_event({'phone': None, 'clearPhone': True}), {})

        assert response['statusCode'] == 200
        attributes = _sent_attributes(mock_cognito)
        assert attributes['phone_number'] == ''
        assert attributes['phone_number_verified'] == 'false'

    def test_an_unusable_email_alongside_clear_phone_is_reported(self, mock_cognito):
        """An email the request carries is parsed and validated whatever clearPhone says, so an
        unusable one is a 400 rather than a field silently dropped from an otherwise 200 update."""
        response = lambda_handler(_event({'email': '', 'clearPhone': True}), {})

        assert response['statusCode'] == 400
        mock_cognito.admin_update_user_attributes.assert_not_called()

    def test_phone_and_clear_phone_together_are_refused(self, mock_cognito):
        response = lambda_handler(
            _event({'phone': '+15559998888', 'clearPhone': True}), {})

        assert response['statusCode'] == 400
        assert 'clearPhone' in _body(response)['message']
        mock_cognito.admin_update_user_attributes.assert_not_called()

    @pytest.mark.parametrize("value", ["maybe", 1, "", None])
    def test_a_non_boolean_clear_phone_is_refused(self, mock_cognito, value):
        response = lambda_handler(_event({'email': NEW_EMAIL, 'clearPhone': value}), {})

        assert response['statusCode'] == 400
        mock_cognito.admin_update_user_attributes.assert_not_called()
