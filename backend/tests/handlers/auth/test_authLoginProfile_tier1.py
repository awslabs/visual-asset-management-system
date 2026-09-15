# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1 (API-level) authorization on /auth/loginProfile/{userId}.

The self-user branch of the handler serves the caller's own profile, so it applies no
Tier-2 constraint. Route access is therefore decided entirely by the Tier-1
``enforceAPI()`` check.

Two of the outcomes are pinned directly: a caller the enforcer allows reads its profile,
and a caller the enforcer denies gets a 403 while ``enforceAPI()`` is proven to have been
consulted with the request event.

The empty-token outcome needs the indirect assertion below. A request with no
authenticated identity cannot reach the profile whatever Tier 1 decides, because
``authorizerUserId`` is then ``None`` and the self-identity comparison denies it a few
lines later -- so ``statusCode == 403`` on a plain self-user event is satisfied by a
fail-OPEN Tier-1 block just as happily as by the shipped one, and neither constructs the
enforcer. What only the shipped pre-set ``method_allowed_on_api = False`` produces is a
denial that lands *before* the path ``userId`` is validated: a malformed userId with no
identity is a 403, not the 400 a fail-open block would fall through to. The positive
control asserts the same malformed userId does reach validation (400) once an identity is
present, so the 403 cannot be a validator that quietly accepts everything.

Guards FIX-065 (S2-BACKEND-141): an "API-level only" classification still requires Tier 1 to
run, and requires the route to be scoped to the requesting user.
"""

import json
import pytest
from unittest.mock import patch

from backend.backend.handlers.auth.authLoginProfile import lambda_handler

_SELF_USER = "test-user-id"
# Rejected by the USERID validator (spaces and '!' are outside its character class).
_MALFORMED_USER_ID = "no spaces allowed!"


def _get_event(user_id=_SELF_USER):
    return {
        'requestContext': {
            'http': {
                'method': 'GET',
                'path': f'/auth/loginProfile/{user_id}',
            }
        },
        'pathParameters': {'userId': user_id},
        'headers': {'Authorization': 'Bearer test-token'},
    }


def _post_event(user_id=_SELF_USER):
    return {
        'requestContext': {
            'http': {
                'method': 'POST',
                'path': f'/auth/loginProfile/{user_id}',
            }
        },
        'pathParameters': {'userId': user_id},
        'body': json.dumps({'email': 'test@example.com'}),
        'headers': {'Authorization': 'Bearer test-token'},
    }


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_get_allowed_on_api_returns_profile(mock_casbin, mock_user_table, mock_claims):
    """A caller whose role grants the /auth/loginProfile route reads its own profile."""
    mock_claims.return_value = {"tokens": [_SELF_USER], "roles": ["someRole"], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = True
    mock_user_table.get_item.return_value = {
        'Item': {'userId': _SELF_USER, 'email': 'test@example.com'}
    }

    event = _get_event()
    response = lambda_handler(event, {})

    assert response['statusCode'] == 200
    assert json.loads(response['body'])['userId'] == _SELF_USER
    # The route/method pair is what was submitted to the enforcer.
    mock_casbin.return_value.enforceAPI.assert_called_once_with(event)


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_get_denied_on_api_returns_403_without_reading_profile(
    mock_casbin, mock_user_table, mock_claims
):
    """A caller whose constraints do not grant the route is denied even for its own
    userId -- the self-user branch is gated by Tier 1, not a substitute for it."""
    mock_claims.return_value = {"tokens": [_SELF_USER], "roles": ["someRole"], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = False
    mock_user_table.get_item.return_value = {
        'Item': {'userId': _SELF_USER, 'email': 'test@example.com'}
    }

    event = _get_event()
    response = lambda_handler(event, {})

    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'
    # The path userId equals the caller's own identity, so the self-identity comparison
    # succeeds and the 403 can only be the enforcer's decision -- which was taken on this
    # very request, not skipped.
    mock_casbin.return_value.enforceAPI.assert_called_once_with(event)
    mock_user_table.get_item.assert_not_called()


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_post_denied_on_api_returns_403_without_writing_profile(
    mock_casbin, mock_user_table, mock_claims
):
    """A denied POST writes nothing -- the profile upsert sits behind the Tier 1 check."""
    mock_claims.return_value = {"tokens": [_SELF_USER], "roles": ["someRole"], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = False

    event = _post_event()
    response = lambda_handler(event, {})

    assert response['statusCode'] == 403
    mock_casbin.return_value.enforceAPI.assert_called_once_with(event)
    mock_user_table.put_item.assert_not_called()


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_no_identity_denied_before_the_path_userId_is_validated(
    mock_casbin, mock_user_table, mock_claims
):
    """No authenticated identity denies on the pre-set flag alone.

    The userId here is malformed, so a Tier-1 block that failed open would fall through to
    the validator and answer 400. The 403 is what pins the ordering: the pre-set
    ``method_allowed_on_api = False`` denies first. The enforcer is set to allow and must
    not be constructed at all -- there is no identity to evaluate.
    """
    mock_claims.return_value = {"tokens": [], "roles": [], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = True

    response = lambda_handler(_get_event(user_id=_MALFORMED_USER_ID), {})

    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'
    mock_casbin.assert_not_called()
    mock_user_table.get_item.assert_not_called()


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_no_identity_denied_on_post_before_validation(
    mock_casbin, mock_user_table, mock_claims
):
    """The fail-closed path also blocks the mutating method ahead of validation."""
    mock_claims.return_value = {"tokens": [], "roles": [], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = True

    response = lambda_handler(_post_event(user_id=_MALFORMED_USER_ID), {})

    assert response['statusCode'] == 403
    mock_casbin.assert_not_called()
    mock_user_table.put_item.assert_not_called()


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_malformed_path_userId_reaches_validation_when_identity_is_present(
    mock_casbin, mock_user_table, mock_claims
):
    """Positive control for the two fail-closed cases above.

    The same malformed userId is answered with a 400 once an identity is present and Tier 1
    allows the route, so the userId really is rejected by the validator and the 403 above is
    the authorization decision rather than a validator that accepts anything.
    """
    mock_claims.return_value = {"tokens": [_SELF_USER], "roles": ["someRole"], "mfaEnabled": False}
    mock_casbin.return_value.enforceAPI.return_value = True

    response = lambda_handler(_get_event(user_id=_MALFORMED_USER_ID), {})

    assert response['statusCode'] == 400
    assert 'userid' in json.loads(response['body'])['message'].lower()
    mock_user_table.get_item.assert_not_called()


@pytest.mark.unit
@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.CasbinEnforcer')
def test_other_user_denied_even_when_allowed_on_api(mock_casbin, mock_user_table, mock_claims):
    """Route access does not grant another user's profile -- the administration route is
    not implemented, so a mismatched path userId is denied regardless of Tier 1."""
    mock_claims.return_value = {"tokens": [_SELF_USER], "roles": ["admin"], "mfaEnabled": True}
    mock_casbin.return_value.enforceAPI.return_value = True

    response = lambda_handler(_get_event(user_id="another-user-id"), {})

    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'
    mock_user_table.get_item.assert_not_called()
