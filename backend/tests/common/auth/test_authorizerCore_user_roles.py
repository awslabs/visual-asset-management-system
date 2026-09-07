# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Role resolution in the authorizer core.

The authorizer resolves a user's roles from the user roles table and passes them to handler
lambdas as the ``vams:roles`` authorizer context value. Resolving here rather than at token
issuance is what makes roles available in every JWT auth mode — a Cognito pre-token-generation
trigger only runs for Cognito, so an external OAuth IDP deployment would otherwise carry no
roles at all (audit logs would record an empty role list for every event).

These tests cover the per-user cache, the fail-open behavior, and the fact that the freshly
read value replaces any stale ``vams:roles`` claim carried inside a presented token.

They also cover how long an EMPTY result stays cached. Routing the API-key branch through this
helper gave it the helper's cache, and that branch DENIES a request whose user resolves to no
roles -- so holding an empty list for the full TTL keeps denying a machine identity for up to a
minute after its role was granted, where an un-cached read in ``verify_api_key`` sees the grant
on the next request that reaches the authorizer.

That last qualifier is the whole of it: the window an operator measures is NOT this TTL. API
Gateway caches the Deny policy the authorizer returns for authorizerResultTtlInSeconds -- 30
seconds on the authenticated scheme, keyed on the Authorization header an API key is itself
presented in -- so the same key is refused from that cache without the authorizer running at
all. The shorter empty-role TTL therefore takes the grant latency from about a minute to about
30 seconds, not to 5. The tests below assert the RELATION (the empty TTL under both the
populated TTL and the authorizer cache) rather than a 5-second window no caller can observe.
"""
import inspect
import json
import re

import pytest
from unittest.mock import patch
from backend.backend.common.auth import authorizerCore as core

# The authenticated REQUEST authorizer's result cache, in seconds. The VamsAuthorizer
# security scheme in infra/lib/nestedStacks/apiLambda/constructs/buildOpenApiSpec.ts sets
# authorizerResultTtlInSeconds to this, keyed on method.request.header.Authorization --
# the header an API key is presented in. Repeated here because it is the term that
# dominates the empty-role TTL and nothing in the backend otherwise records it.
API_GATEWAY_AUTHORIZER_RESULT_TTL_SECONDS = 30


class _FakeRolesTable:
    """Minimal stand-in for the user roles DynamoDB table that counts queries."""

    def __init__(self, items=None, raises=False):
        self._items = items if items is not None else []
        self._raises = raises
        self.query_count = 0

    def query(self, **kwargs):
        self.query_count += 1
        if self._raises:
            raise RuntimeError("table unavailable")
        return {"Items": self._items}


@pytest.fixture(autouse=True)
def _clear_roles_cache():
    """The role cache is module-level state; isolate every test from its neighbours."""
    core._user_roles_cache.clear()
    yield
    core._user_roles_cache.clear()


@pytest.mark.unit
class TestLookupUserRoles:
    def test_returns_role_names_from_table(self):
        table = _FakeRolesTable([{"roleName": "admin"}, {"roleName": "viewer"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin", "viewer"]

    def test_skips_items_missing_role_name(self):
        table = _FakeRolesTable([{"roleName": "admin"}, {"userId": "u1"}, {"roleName": ""}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin"]

    def test_empty_user_id_returns_empty_without_querying(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("") == []
            assert core._lookup_user_roles(None) == []
        assert table.query_count == 0

    def test_second_call_within_ttl_is_served_from_cache(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin"]
            assert core._lookup_user_roles("u1") == ["admin"]
        assert table.query_count == 1

    def test_empty_result_is_cached_so_roleless_user_does_not_requery(self):
        table = _FakeRolesTable([])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == []
            assert core._lookup_user_roles("u1") == []
        assert table.query_count == 1

    def test_expired_cache_entry_requeries_and_picks_up_role_change(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin"]
            # Force expiry, then revoke the role in the table — the next call must see it.
            core._user_roles_cache["u1"]["expiry"] = 0
            table._items = [{"roleName": "viewer"}]
            assert core._lookup_user_roles("u1") == ["viewer"]
        assert table.query_count == 2

    def test_cache_is_per_user(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin"]
            table._items = [{"roleName": "viewer"}]
            assert core._lookup_user_roles("u2") == ["viewer"]
            # u1 still served from its own cache entry
            assert core._lookup_user_roles("u1") == ["admin"]
        assert table.query_count == 2

    def test_unavailable_table_returns_empty(self):
        with patch.object(core, "_get_user_roles_table", return_value=None):
            assert core._lookup_user_roles("u1") == []

    def test_query_error_returns_empty(self):
        table = _FakeRolesTable(raises=True)
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == []

    def test_query_error_falls_back_to_stale_cached_roles(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "_get_user_roles_table", return_value=table):
            assert core._lookup_user_roles("u1") == ["admin"]
            core._user_roles_cache["u1"]["expiry"] = 0
            table._raises = True
            # Rather than dropping roles on a transient table error, serve the stale value.
            assert core._lookup_user_roles("u1") == ["admin"]

    def test_a_role_granted_after_an_empty_lookup_is_seen_well_inside_the_full_ttl(self):
        """Grant latency: an empty result must not be held for the whole USER_ROLES_CACHE_TTL.

        Stated as "the grant is visible before the full TTL elapses" rather than as an exact
        window, so not caching an empty result, bypassing the cache on empty, and giving an
        empty result its own shorter TTL all satisfy it. The paired control is
        ``test_empty_result_is_cached_so_roleless_user_does_not_requery`` above: an immediate
        second lookup is still served from cache, so this asks for a shorter window and not
        for the removal of negative caching.
        """
        table = _FakeRolesTable([])
        clock = {"now": 1_000_000.0}
        with patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core.time, "time", side_effect=lambda: clock["now"]):
            assert core._lookup_user_roles("u1") == []
            # The role is granted a moment later, still inside the populated-entry TTL.
            table._items = [{"roleName": "admin"}]
            clock["now"] += core.USER_ROLES_CACHE_TTL - 1
            assert core._lookup_user_roles("u1") == ["admin"], (
                "the empty result was still cached almost a full USER_ROLES_CACHE_TTL after "
                "the role was granted")

    def test_api_key_identity_authenticates_after_a_grant_without_the_full_ttl_elapsing(self):
        """The same latency, through the caller that makes it a denial rather than a detail.

        On the JWT path roles are informational -- Casbin re-reads them when building policy --
        so a stale empty list only degrades the context. ``verify_api_key`` instead DENIES the
        request outright, so what an operator sees is a machine identity still refused with
        "No roles for API key user" after the role was granted.

        The clock this test advances is the role cache's. The refusal an operator actually
        waits out is floored by the 30-second API Gateway authorizer result cache, which
        replays the Deny for the same key without invoking the authorizer -- so what this
        asserts is that the role cache stops being the dominating term, taking the wait from
        about a minute to about 30 seconds rather than to USER_ROLES_EMPTY_CACHE_TTL.
        """
        record = {"apiKeyId": "k1", "isActive": "true", "userId": "u1", "expiresAt": ""}
        table = _FakeRolesTable([])
        clock = {"now": 2_000_000.0}
        with patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core, "_lookup_api_key_by_hash", return_value=record), \
             patch.object(core.time, "time", side_effect=lambda: clock["now"]):
            denied = core.verify_api_key("vams_rawkey")
            # Control: with no roles assigned the key really is denied, so the assertion
            # below records a state change rather than the starting state.
            assert denied.get("denied") is True, denied
            table._items = [{"roleName": "pipeline"}]
            clock["now"] += core.USER_ROLES_CACHE_TTL - 1
            claims = core.verify_api_key("vams_rawkey")

        assert claims is not None and "denied" not in claims, claims
        assert json.loads(claims["vams:roles"]) == ["pipeline"]


@pytest.mark.unit
class TestEmptyRoleTtlAgainstTheCachesAroundIt:
    """The empty-role TTL only buys anything while it stays under the caches around it.

    Stated as two upper bounds and never as ``== 5``: every shorter value is at least as
    responsive, so a pin on the number would fail a strictly safer implementation, while a value
    that climbs past either bound silently restores the delay the shorter TTL was written for.
    """

    def test_an_empty_result_expires_before_a_populated_one(self):
        assert core.USER_ROLES_EMPTY_CACHE_TTL < core.USER_ROLES_CACHE_TTL

    def test_the_empty_ttl_stays_under_the_authorizer_result_cache(self):
        # Above the authorizer cache, the role cache is again the term that holds a granted
        # machine identity out, which is the state this TTL exists to leave behind.
        assert core.USER_ROLES_EMPTY_CACHE_TTL <= API_GATEWAY_AUTHORIZER_RESULT_TTL_SECONDS, (
            "an empty-role TTL above the API Gateway authorizer result cache puts the role "
            "cache back in charge of how long a granted machine identity keeps being denied")

    def test_the_constant_records_the_cache_that_dominates_it(self):
        """The number alone reads as a 5-second grant latency, which no caller can observe.

        Guards the CLAIM rather than the wording: the block between the two TTL constants has to
        name the API Gateway authorizer result cache and carry the figure that floors the window,
        in any phrasing. Without that, the next reader tunes this constant expecting an effect the
        request path cannot deliver. The figure is coupled to
        API_GATEWAY_AUTHORIZER_RESULT_TTL_SECONDS above, so changing the CDK TTL fails here until
        the explanation is corrected with it.
        """
        source = inspect.getsource(core)
        before_empty, _, _ = source.partition("USER_ROLES_EMPTY_CACHE_TTL =")
        block = before_empty.rsplit("USER_ROLES_CACHE_TTL =", 1)[-1]
        # Control: the comment block really was located, so nothing below is asserted against
        # an empty string.
        assert "empty" in block.lower(), source[:400]

        names_the_cache = any(re.search(pattern, block, re.I) for pattern in (
            r"authorizerResultTtl",
            r"authoriz\w*[\s-]+(result[\s-]+)?cache",
            r"cache[^.]{0,60}authoriz",
        ))
        assert names_the_cache, (
            "the empty-role TTL is documented without the API Gateway authorizer result cache "
            "that dominates it: " + block)
        assert str(API_GATEWAY_AUTHORIZER_RESULT_TTL_SECONDS) in block, (
            "the explanation does not carry the authorizer cache figure that floors the "
            "window: " + block)

    def test_the_lookup_docstring_records_it_too(self):
        """The same claim, in the second place it is made: the docstring at the call site."""
        doc = inspect.getdoc(core._lookup_user_roles) or ""
        assert "USER_ROLES_EMPTY_CACHE_TTL" in doc, doc  # control: this is the right docstring
        assert re.search(r"authoriz\w*[\s-]+(result[\s-]+)?cache", doc, re.I), doc
        assert str(API_GATEWAY_AUTHORIZER_RESULT_TTL_SECONDS) in doc, doc


def _jwt_event():
    return {
        "headers": {"Authorization": "Bearer sometoken"},
        "path": "/database/db1/assets",
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
    }


@pytest.mark.unit
class TestAuthenticateRequestPopulatesRoles:
    def test_external_mode_gets_roles_in_context(self):
        """The defect this guards: external OAuth IDP tokens carry no vams:roles claim
        (no Cognito pre-token-generation trigger runs), so the authorizer must supply them."""
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core, "resolve_mfa_enabled", return_value=False), \
             patch.object(core, "verify_external_jwt", return_value={"sub": "u1"}):
            result = core.authenticate_request(_jwt_event(), fronted="none")

        assert result["authorized"] is True
        assert json.loads(result["context"]["vams:roles"]) == ["admin"]

    def test_cognito_mode_gets_roles_in_context(self):
        table = _FakeRolesTable([{"roleName": "viewer"}])
        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core, "resolve_mfa_enabled", return_value=False), \
             patch.object(core, "verify_cognito_jwt",
                          return_value={"cognito:username": "u1", "sub": "s1"}):
            result = core.authenticate_request(_jwt_event(), fronted="none")

        assert result["authorized"] is True
        assert json.loads(result["context"]["vams:roles"]) == ["viewer"]

    def test_stale_token_roles_claim_is_overwritten_by_table_value(self):
        """A token minted before a role change still carries the old vams:roles claim.
        The table value must win, or a revocation would not take effect until re-login."""
        table = _FakeRolesTable([{"roleName": "viewer"}])
        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core, "resolve_mfa_enabled", return_value=False), \
             patch.object(core, "verify_cognito_jwt", return_value={
                 "cognito:username": "u1",
                 "vams:roles": '["admin"]',  # stale claim baked into the token
             }):
            result = core.authenticate_request(_jwt_event(), fronted="none")

        assert json.loads(result["context"]["vams:roles"]) == ["viewer"]

    def test_roles_resolved_for_username_claim_when_no_cognito_username(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        captured = {}

        def _capture(user_id):
            captured["user_id"] = user_id
            return ["admin"]

        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "_lookup_user_roles", side_effect=_capture), \
             patch.object(core, "resolve_mfa_enabled", return_value=False), \
             patch.object(core, "verify_external_jwt",
                          return_value={"username": "byUsername", "sub": "s1"}):
            core.authenticate_request(_jwt_event(), fronted="none")

        assert captured["user_id"] == "byUsername"

    def test_api_key_path_keeps_its_own_roles_and_is_not_overwritten(self):
        """The API key branch already resolves roles and returns before the JWT path, so it
        must not be affected by the JWT-path role resolution."""
        event = {
            "headers": {"Authorization": "vams_testkey"},
            "path": "/database/db1/assets",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        }
        api_claims = {
            "sub": "apiuser",
            "vams:tokens": '["apiuser"]',
            "vams:roles": '["pipeline"]',
            "vams:authMethod": "apiKey",
        }
        with patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "verify_api_key", return_value=api_claims):
            result = core.authenticate_request(event, fronted="none")

        assert result["authorized"] is True
        assert json.loads(result["context"]["vams:roles"]) == ["pipeline"]

    def test_roles_not_resolved_when_jwt_verification_fails(self):
        table = _FakeRolesTable([{"roleName": "admin"}])
        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "ALLOWED_IP_RANGES", []), \
             patch.object(core, "IGNORED_PATHS", []), \
             patch.object(core, "_get_user_roles_table", return_value=table), \
             patch.object(core, "verify_external_jwt", return_value=None):
            result = core.authenticate_request(_jwt_event(), fronted="none")

        assert result["authorized"] is False
        assert result["context"] is None
        assert table.query_count == 0
