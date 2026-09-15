# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two boundaries where a user id becomes an identity: the claims context, and user creation.

User ids stay Unicode (see `tests/common/test_userid_identity_normalization.py` for the rule itself and
for why an ASCII-only class was rejected). What keeps two spellings from becoming two identities is
NFKC normalization on every path that writes or looks up a user id, plus a confusable-skeleton check at
creation. Two of those paths cannot be reached from the validators alone:

  * `request_to_claims` -- the caller's own identity. No request body carries it, so it is normalized
    where every handler receives it. If it were not, a stored id written in the normalized form would
    never match the token that produced it.
  * `create_cognito_user` -- the only place a new user id enters the pool, and therefore the only
    place a lookalike can still be refused. An id already in the pool is never re-checked.
"""

import os

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

os.environ.setdefault('COGNITO_ENABLED', 'true')
os.environ.setdefault('USER_POOL_ID', 'test-pool-id')

from backend.backend.handlers.auth import request_to_claims  # noqa: E402
from backend.backend.handlers.auth import cognitoUserService as svc  # noqa: E402

MOCK_POOL_ID = 'us-east-1_testpool'

FULLWIDTH_A = 'ａ'      # NFKC folds this to 'a'
CYRILLIC_A = 'а'       # reads as 'a', and NFKC does not fold it
JAPANESE_USER_ID = '山田.太郎'


@pytest.mark.unit
class TestTheClaimsBoundaryNormalizes:
    """The token list keys per-user lookups, is written as createdBy/modifiedBy, and is what Casbin
    compares a constraint's userId against."""

    def test_a_compatibility_spelling_in_the_token_claim_is_normalized(self):
        event = {'requestContext': {'authorizer': {
            'vams:tokens': '["' + FULLWIDTH_A + 'dmin.user"]'}}}
        assert request_to_claims(event)['tokens'] == ['admin.user']

    def test_a_username_claim_is_normalized_too(self):
        """The fallback claims (cognito:username, username, sub, upn, email) reach the same list."""
        event = {'requestContext': {'authorizer': {
            'cognito:username': FULLWIDTH_A + 'dmin.user'}}}
        assert request_to_claims(event)['tokens'] == ['admin.user']

    def test_an_ascii_identity_is_unchanged(self):
        """CONTROL: normalization must be a no-op for the ids every existing deployment holds."""
        event = {'requestContext': {'authorizer': {
            'vams:tokens': '["first.last@example.com"]', 'vams:roles': '["admin"]'}}}
        claims = request_to_claims(event)
        assert claims['tokens'] == ['first.last@example.com']
        assert claims['roles'] == ['admin']

    def test_a_non_latin_identity_is_preserved(self):
        """OVER-TIGHTENING CATCHER: an external IDP's non-Latin username must reach the handler
        intact, not stripped or rejected."""
        event = {'requestContext': {'authorizer': {'sub': JAPANESE_USER_ID}}}
        assert request_to_claims(event)['tokens'] == [JAPANESE_USER_ID]

    def test_a_lambda_cross_call_identity_is_normalized(self):
        assert request_to_claims(
            {'lambdaCrossCall': {'userName': FULLWIDTH_A + 'dmin.user'}})['tokens'] == ['admin.user']

    def test_the_system_user_cross_call_default_is_unchanged(self):
        """CONTROL: SYSTEM_USER is compared as an exact string by handlers all over the backend."""
        assert request_to_claims({'lambdaCrossCall': {}})['tokens'] == ['SYSTEM_USER']


@pytest.fixture
def cognito(request):
    """A stub Cognito client with a scripted user pool.

    The pool contents are passed as the fixture parameter: either a list of usernames (one page) or a
    list of pages, each a list of usernames.
    """
    pages = getattr(request, 'param', [])
    if pages and not isinstance(pages[0], list):
        pages = [pages]
    pages = pages or [[]]

    responses = []
    for index, page in enumerate(pages):
        response = {'Users': [{'Username': name} for name in page]}
        if index < len(pages) - 1:
            response['PaginationToken'] = f'page-{index + 1}'
        responses.append(response)

    client = MagicMock()
    client.list_users.side_effect = list(responses)
    with patch.object(svc, 'cognito_client', client), \
         patch.object(svc, 'cognito_enabled', True), \
         patch.object(svc, 'user_pool_id', MOCK_POOL_ID):
        yield client


def _create(user_id):
    return svc.create_cognito_user({'userId': user_id, 'email': 'new.user@example.com'}, {})


@pytest.mark.unit
class TestCreationRefusesALookalike:

    @pytest.mark.parametrize('cognito', [['reader', 'admin', 'operator.one']], indirect=True)
    def test_a_cross_script_lookalike_of_an_existing_user_is_refused(self, cognito):
        with pytest.raises(svc.VAMSGeneralErrorResponse) as raised:
            _create(CYRILLIC_A + 'dmin')
        assert 'too similar' in str(raised.value)
        cognito.admin_create_user.assert_not_called()

    @pytest.mark.parametrize('cognito', [[['reader', 'operator.one'], ['admin']]], indirect=True)
    def test_the_pool_is_walked_past_the_first_page(self, cognito):
        """Cognito serves at most 60 usernames per page, so a check that read one page would miss
        every existing user beyond it -- and pass."""
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            _create(CYRILLIC_A + 'dmin')
        # A LOWER bound: the claim is that the scan continued past the first page. The
        # PaginationToken assertion below indexes call 2, so it cannot pass on fewer.
        assert cognito.list_users.call_count >= 2
        assert cognito.list_users.call_args_list[1].kwargs['PaginationToken'] == 'page-1'
        cognito.admin_create_user.assert_not_called()

    @pytest.mark.parametrize('cognito', [['admin']], indirect=True)
    def test_creation_is_refused_when_the_pool_cannot_be_enumerated(self, cognito):
        """Uniqueness that cannot be verified is not uniqueness: the user is not created."""
        cognito.list_users.side_effect = ClientError(
            {'Error': {'Code': 'InternalErrorException', 'Message': 'boom'}}, 'ListUsers')
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            _create(CYRILLIC_A + 'dmin')
        cognito.admin_create_user.assert_not_called()


@pytest.mark.unit
class TestCreationStillAllowsLegitimateUserIds:
    """OVER-TIGHTENING CATCHERS. The check refuses a lookalike of an EXISTING id and nothing else."""

    @pytest.mark.parametrize('cognito', [['admin', 'operator.one']], indirect=True)
    def test_a_non_colliding_non_ascii_user_is_created(self, cognito):
        result = _create(JAPANESE_USER_ID)
        assert result.userId == JAPANESE_USER_ID
        assert cognito.admin_create_user.called, 'the user was never created'
        assert cognito.admin_create_user.call_count <= 1, 'the user was created twice'
        assert cognito.admin_create_user.call_args.kwargs['Username'] == JAPANESE_USER_ID

    @pytest.mark.parametrize('cognito', [['admin', 'operator.one']], indirect=True)
    def test_an_ordinary_ascii_user_is_created(self, cognito):
        result = _create('new.user@example.com')
        assert result.userId == 'new.user@example.com'
        assert cognito.admin_create_user.called, 'the user was never created'
        assert cognito.admin_create_user.call_count <= 1, 'the user was created twice'

    @pytest.mark.parametrize('cognito', [[]], indirect=True)
    def test_the_created_username_is_the_normalized_one(self, cognito):
        """Normalization has to reach the Cognito call, not only the validation before it: the pool
        must hold the same spelling the rest of VAMS stores."""
        result = _create(FULLWIDTH_A + 'dmin.user')
        assert result.userId == 'admin.user'
        assert cognito.admin_create_user.call_args.kwargs['Username'] == 'admin.user'
