# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The authorizer's role lookup reads the same spelling of a user id that the row was written with.

A user-role row is stored under the normalized user id (`CreateUserRolesRequestModel` NFKC-normalizes
before validating), while the id arriving here is whatever the identity provider issued. The two
therefore have to be reconciled at the read, or the query looks for a partition key that no row
carries and the user resolves to no roles at all -- which is the asymmetry NFKC normalization exists
to remove rather than to introduce. It is normalized inside `_lookup_user_roles`, so both callers (the
JWT path and the API-key path) and the per-user cache all agree on one spelling per identity.

The consequence differs by caller, and both are covered below: on the JWT path an empty result
degrades `vams:roles` -- the value handlers record in audit entries -- while `verify_api_key` denies
the request outright, so a machine identity holding a role would be refused.
"""
import json

import pytest
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core

# NFKC folds this onto 'a'. An id an IDP can legitimately issue, and the exact case that would
# otherwise be written one way and read another.
FULLWIDTH_A = 'ａ'
NORMALIZED = 'admin.user'
ISSUED = FULLWIDTH_A + 'dmin.user'

# Ordinary non-Latin id: NFKC leaves it alone, so the lookup must use it verbatim.
JAPANESE_USER_ID = '山田.太郎'


class _RolesTableByUserId:
    """User roles table stubbed as it really is: rows keyed on one exact userId."""

    def __init__(self, rows_by_user_id):
        self._rows = rows_by_user_id
        self.queried = []

    def query(self, **kwargs):
        # The handler builds KeyConditionExpression via boto3's Key(...).eq(); read the value back
        # off the condition so the assertion is about the id queried, not about how it is expressed.
        expression = kwargs['KeyConditionExpression'].get_expression()
        assert expression['operator'] == '=', expression   # control: the shape assumed below
        user_id = expression['values'][1]
        self.queried.append(user_id)
        return {'Items': [{'roleName': role} for role in self._rows.get(user_id, [])]}


@pytest.fixture(autouse=True)
def _clear_roles_cache():
    """The role cache is module-level state, and this file writes entries under two spellings."""
    core._user_roles_cache.clear()
    yield
    core._user_roles_cache.clear()


@pytest.mark.unit
class TestTheRoleLookupNormalizesTheUserId:

    def test_a_row_written_under_the_normalized_id_is_found(self):
        """POSITIVE CONTROL for the fix: the row exists, and only normalizing finds it."""
        table = _RolesTableByUserId({NORMALIZED: ['admin']})
        with patch.object(core, '_get_user_roles_table', return_value=table):
            assert core._lookup_user_roles(ISSUED) == ['admin']
        assert table.queried == [NORMALIZED], (
            'the lookup queried the id as issued, so it missed the row stored under the '
            'normalized one')

    def test_the_unnormalized_spelling_really_would_have_missed(self):
        """CONTROL that the assertion above records a state change rather than the starting state:
        against the same table, querying the issued spelling finds nothing."""
        table = _RolesTableByUserId({NORMALIZED: ['admin']})
        assert table.query(
            KeyConditionExpression=core.DDBKey('userId').eq(ISSUED))['Items'] == []

    def test_both_spellings_share_one_cache_entry(self):
        """Keying the cache on the normalized id is what keeps the two callers from disagreeing."""
        table = _RolesTableByUserId({NORMALIZED: ['admin']})
        with patch.object(core, '_get_user_roles_table', return_value=table):
            assert core._lookup_user_roles(ISSUED) == ['admin']
            assert core._lookup_user_roles(NORMALIZED) == ['admin']
        assert table.queried == [NORMALIZED]
        assert list(core._user_roles_cache) == [NORMALIZED]

    def test_an_ascii_user_id_is_queried_unchanged(self):
        """CONTROL: normalization is a no-op for the ids every existing deployment holds, so no
        current lookup changes."""
        table = _RolesTableByUserId({'first.last@example.com': ['viewer']})
        with patch.object(core, '_get_user_roles_table', return_value=table):
            assert core._lookup_user_roles('first.last@example.com') == ['viewer']
        assert table.queried == ['first.last@example.com']

    def test_a_non_latin_user_id_is_queried_verbatim(self):
        """OVER-TIGHTENING CATCHER: NFKC folds compatibility spellings, not scripts."""
        table = _RolesTableByUserId({JAPANESE_USER_ID: ['viewer']})
        with patch.object(core, '_get_user_roles_table', return_value=table):
            assert core._lookup_user_roles(JAPANESE_USER_ID) == ['viewer']
        assert table.queried == [JAPANESE_USER_ID]

    def test_an_absent_user_id_still_short_circuits(self):
        """CONTROL: the empty guard runs before normalization, so no query is issued."""
        table = _RolesTableByUserId({NORMALIZED: ['admin']})
        with patch.object(core, '_get_user_roles_table', return_value=table):
            assert core._lookup_user_roles('') == []
            assert core._lookup_user_roles(None) == []
        assert table.queried == []


def _jwt_event():
    return {
        'headers': {'Authorization': 'Bearer sometoken'},
        'path': '/database/db1/assets',
        'requestContext': {'identity': {'sourceIp': '203.0.113.7'}},
    }


@pytest.mark.unit
class TestTheEffectOnEachCaller:
    """The two places an unfound role list is visible, so the fix is pinned by outcome as well."""

    def test_the_jwt_context_carries_the_roles_of_the_normalized_identity(self):
        """vams:roles is what handlers record in audit entries, and it is the value an operator
        reads back when checking who acted."""
        table = _RolesTableByUserId({NORMALIZED: ['admin']})
        with patch.object(core, 'AUTH_MODE', 'cognito'), \
             patch.object(core, 'ALLOWED_IP_RANGES', []), \
             patch.object(core, 'IGNORED_PATHS', []), \
             patch.object(core, '_get_user_roles_table', return_value=table), \
             patch.object(core, 'resolve_mfa_enabled', return_value=False), \
             patch.object(core, 'verify_cognito_jwt',
                          return_value={'cognito:username': ISSUED, 'sub': 's1'}):
            result = core.authenticate_request(_jwt_event(), fronted='none')

        assert result['authorized'] is True
        assert json.loads(result['context']['vams:roles']) == ['admin']

    def test_an_api_key_whose_user_holds_a_role_is_not_denied(self):
        """The API-key branch denies on an empty role list, so here the miss is a refused request
        rather than a degraded context."""
        record = {'apiKeyId': 'k1', 'isActive': 'true', 'userId': ISSUED, 'expiresAt': ''}
        table = _RolesTableByUserId({NORMALIZED: ['pipeline']})
        with patch.object(core, '_get_user_roles_table', return_value=table), \
             patch.object(core, '_lookup_api_key_by_hash', return_value=record):
            claims = core.verify_api_key('vams_rawkey')

        assert claims is not None and 'denied' not in claims, claims
        assert json.loads(claims['vams:roles']) == ['pipeline']
