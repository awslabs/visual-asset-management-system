# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging of the authorizer's role lookup (backend/CLAUDE.md Rule 14).

``_lookup_user_roles`` builds the ``vams:roles`` authorizer context value that every handler
Lambda and every audit record sees. A DynamoDB query returns at most 1 MB and reports the rest
only through ``LastEvaluatedKey``, so reading one page gives a user with many role assignments a
silently short role list — and the result is then cached under that user for the cache TTL, so
one truncated read serves every request in the window.

The API-KEY branch is covered separately at the bottom of this file. ``verify_api_key`` builds
its own ``vams:roles`` for a machine identity and returns before the JWT path's role resolution
is reached, so completeness there is a second, independent property: a fix applied to
``_lookup_user_roles`` alone leaves every API-key request reading one page.

Behaviour of the cache, the fail-open path and the claim override lives in
``test_authorizerCore_user_roles.py``; this file covers only completeness of the read.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.common.auth import authorizerCore as core


def _cursor(exclusive_start_key):
    return json.dumps(exclusive_start_key, sort_keys=True)


class _PagedRolesTable:
    """Serves canned pages keyed on ``ExclusiveStartKey`` rather than on call order.

    Keying on the cursor keeps the assertion at "the cursor is threaded" instead of "exactly N
    reads happened", so an extra or repeated read still resolves to the right page. ``MAX_READS``
    turns a loop that never advances into a test failure with a message instead of a hang.
    """

    MAX_READS = 10

    def __init__(self, *pages):
        self.pages = {_cursor(None): pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self.pages[_cursor(previous["LastEvaluatedKey"])] = page
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > self.MAX_READS:
            raise AssertionError(
                "the paged read did not advance: the previous page's LastEvaluatedKey is not "
                "being passed back as ExclusiveStartKey")
        return self.pages[_cursor(kwargs.get("ExclusiveStartKey"))]


PAGE_ONE_CURSOR = {"userId": "u1", "roleName": "roleOnPageOne"}


@pytest.fixture(autouse=True)
def _clear_roles_cache():
    """The role cache is module-level state; isolate every test from its neighbours."""
    core._user_roles_cache.clear()
    yield
    core._user_roles_cache.clear()


@pytest.mark.unit
class TestRoleLookupPagesToExhaustion:
    def test_a_role_on_a_later_page_is_still_returned(self):
        table = _PagedRolesTable(
            {"Items": [{"roleName": "roleOnPageOne"}], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [{"roleName": "roleOnPageTwo"}]},
        )

        with patch.object(core, "_get_user_roles_table", return_value=table):
            roles = core._lookup_user_roles("u1")

        assert roles == ["roleOnPageOne", "roleOnPageTwo"]
        # A later read must carry the first page's cursor, or it re-reads page one forever.
        assert PAGE_ONE_CURSOR in [call.get("ExclusiveStartKey") for call in table.calls]

    def test_the_full_set_is_what_gets_cached(self):
        """The truncation would otherwise be served from cache for the whole TTL."""
        table = _PagedRolesTable(
            {"Items": [{"roleName": "roleOnPageOne"}], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [{"roleName": "roleOnPageTwo"}]},
        )

        with patch.object(core, "_get_user_roles_table", return_value=table):
            core._lookup_user_roles("u1")

        assert core._user_roles_cache["u1"]["roles"] == ["roleOnPageOne", "roleOnPageTwo"]

    def test_a_single_page_response_is_returned_without_resuming_from_a_cursor(self):
        """Control: a complete one-page answer must not be read as if a page preceded it."""
        table = _PagedRolesTable({"Items": [{"roleName": "onlyRole"}]})

        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["onlyRole"]

        # No read carried a cursor: there is no earlier page to resume from. Asserted as a
        # property of every read rather than as the exact read SEQUENCE — pinning the list to
        # ``[None]`` fails an implementation that reads once more, which is exactly what keying
        # the pager on the cursor rather than on call order exists to tolerate. The first
        # assertion is the positive control the second one needs: "no read carried a cursor"
        # also holds when nothing was read at all.
        assert table.calls, "the control never read the table at all"
        assert not any("ExclusiveStartKey" in call for call in table.calls), table.calls

    def test_paging_terminates_against_an_unstubbed_reader(self):
        """A MagicMock answers ``.get('LastEvaluatedKey')`` with a truthy Mock forever.

        The loop therefore tests key PRESENCE, which a MagicMock reports as False, so an
        under-stubbed reader ends the loop instead of hanging the suite.
        """
        with patch.object(core, "_get_user_roles_table", return_value=MagicMock()):
            assert core._lookup_user_roles("u1") == []


def _api_key_record(user_id="u1"):
    """A live API key record, so the branch reaches its role read."""
    return {"apiKeyId": "k1", "isActive": "true", "userId": user_id, "expiresAt": ""}


def _verify_api_key_against(table):
    with patch.object(core, "_get_user_roles_table", return_value=table), \
            patch.object(core, "_lookup_api_key_by_hash", return_value=_api_key_record()):
        return core.verify_api_key("vams_rawkey")


@pytest.mark.unit
class TestApiKeyRoleLookupPagesToExhaustion:
    """The same completeness property on the API-KEY branch of the authorizer.

    ``verify_api_key`` builds the machine identity's ``vams:roles`` and returns an authorized
    result before the JWT path's role resolution is reached, so this read is not covered by the
    class above: a one-page read here truncates the roles of every API-key request, and the
    truncated list is what Casbin's audit context and every handler see.

    Asserted on the CLAIMS the branch produces rather than on which reader it used, so sharing
    the paged helper and paging a private query both satisfy it.
    """

    def test_a_role_on_a_later_page_reaches_the_api_key_claims(self):
        table = _PagedRolesTable(
            {"Items": [{"userId": "u1", "roleName": "roleOnPageOne"}],
             "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [{"userId": "u1", "roleName": "roleOnPageTwo"}]},
        )

        claims = _verify_api_key_against(table)

        assert claims is not None and "denied" not in claims, claims
        assert json.loads(claims["vams:roles"]) == ["roleOnPageOne", "roleOnPageTwo"], (
            "the API-key branch reported a short role list; its role read stops at the first "
            "page")
        assert PAGE_ONE_CURSOR in [call.get("ExclusiveStartKey") for call in table.calls]

    def test_a_single_page_machine_identity_still_authenticates(self):
        """Control: the paged read must not deny an ordinary one-page API-key identity."""
        table = _PagedRolesTable({"Items": [{"userId": "u1", "roleName": "onlyRole"}]})

        claims = _verify_api_key_against(table)

        assert claims is not None and "denied" not in claims, claims
        assert json.loads(claims["vams:roles"]) == ["onlyRole"]
        assert claims["vams:tokens"] == json.dumps(["u1"])

    def test_a_roleless_machine_identity_is_still_denied(self):
        """Second control: reading every page must not turn "no roles" into an allow."""
        table = _PagedRolesTable({"Items": []})

        claims = _verify_api_key_against(table)

        assert claims["denied"] is True, claims

    def test_the_api_key_read_terminates_against_an_unstubbed_reader(self):
        with patch.object(core, "_get_user_roles_table", return_value=MagicMock()), \
                patch.object(core, "_lookup_api_key_by_hash", return_value=_api_key_record()):
            claims = core.verify_api_key("vams_rawkey")

        # A bare Mock yields no usable role names, so the identity is denied; what matters is
        # that the read returned at all rather than spinning on a truthy LastEvaluatedKey.
        assert claims is None or claims.get("denied") is True, claims
