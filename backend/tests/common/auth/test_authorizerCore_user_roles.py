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
"""
import json
import pytest
from unittest.mock import patch
from backend.backend.common.auth import authorizerCore as core


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
