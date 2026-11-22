# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The authorizer's lazily resolved tables while resource-name sweeps back off.

The authorizer resolves the user roles and API key table names on first use, through
``common.resourceNames``. After a failed Parameter Store sweep, those lookups are refused at once
for the resolver's back-off window instead of sweeping again on every request, and the authorizer
keeps its failure behaviour: the JWT path carries cached or empty informational roles (handlers
still run their own Casbin checks), and an API-key request is not authorized: this release's API-key
path returns no identity when the table is unavailable, and the key then fails JWT verification.
"""
import pytest
from botocore.exceptions import ClientError

from backend.backend.common.auth import authorizerCore as core

PREFIX = "/vams-test/resourceNames"


class _ThrottledSsm:
    """Every GetParametersByPath call is throttled; ``calls`` counts them (paginated or not)."""

    def __init__(self):
        self.calls = 0

    def get_parameters_by_path(self, **_):
        self.calls += 1
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                           "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetParametersByPath")

    def get_paginator(self, operation):
        fake = self

        class _Paginator:
            def paginate(self, **kwargs):
                yield fake.get_parameters_by_path(**kwargs)

        return _Paginator()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def throttled(monkeypatch):
    """A resourceNames module whose SSM sweep is throttled, wired into the authorizer core, with the
    authorizer's lazy tables and caches cleared. Returns (resourceNames, the SSM fake)."""
    import common.resourceNames as resourceNames
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for key in vars(resourceNames.ResourceKeys).values():
        if isinstance(key, resourceNames.ResourceParamKey):
            for env_name in key.env_var_names:
                monkeypatch.delenv(env_name, raising=False)
    resourceNames._cache = {}
    resourceNames._cache_fetched_at = 0.0
    resourceNames._missing_keys = {}
    resourceNames._failed_sweeps = 0
    resourceNames._sweep_retry_after = 0.0
    clock = _Clock()
    monkeypatch.setattr(resourceNames, "_monotonic", clock.monotonic, raising=False)
    monkeypatch.setattr(resourceNames, "_sleep", clock.sleep, raising=False)
    monkeypatch.setattr(resourceNames, "_uniform", lambda low, high: high, raising=False)
    ssm = _ThrottledSsm()
    monkeypatch.setattr(resourceNames, "_get_ssm_client", lambda: ssm)

    monkeypatch.setattr(core, "get_table_name", resourceNames.get_table_name)
    monkeypatch.setattr(core, "_user_roles_table", None)
    monkeypatch.setattr(core, "_api_key_table", None)
    core._user_roles_cache.clear()
    core._api_key_cache.clear()

    # The cold start's first lookup fails its sweep and opens the back-off window.
    with pytest.raises(Exception):
        resourceNames.get_table_name(resourceNames.ResourceKeys.ASSET_STORAGE_TABLE)
    assert ssm.calls > 0
    ssm.calls = 0
    yield resourceNames, ssm
    core._user_roles_cache.clear()
    core._api_key_cache.clear()


@pytest.mark.unit
class TestAuthorizerInsideTheBackoffWindow:
    def test_the_lazy_tables_are_not_resolved_and_ssm_is_not_read(self, throttled):
        _, ssm = throttled
        assert core._get_user_roles_table() is None
        assert core._get_api_key_table() is None
        assert ssm.calls == 0

    def test_the_jwt_path_carries_cached_or_empty_roles(self, throttled):
        _, ssm = throttled
        assert core._lookup_user_roles("user-without-cache") == []
        # An expired entry still answers when the table cannot be read.
        core._user_roles_cache["user-with-cache"] = {"roles": ["admin"], "expiry": 0}
        assert core._lookup_user_roles("user-with-cache") == ["admin"]
        assert ssm.calls == 0

    def test_the_api_key_request_is_not_authorized(self, throttled, monkeypatch):
        _, ssm = throttled
        assert core.verify_api_key("vams_unknown-key-for-test") is None
        monkeypatch.setattr(core, "IGNORED_PATHS", ["/api/version"])
        monkeypatch.setattr(core, "AUTH_MODE", "cognito")
        monkeypatch.setattr(core, "verify_cognito_jwt", lambda token: None)
        event = {"requestContext": {"identity": {"sourceIp": "203.0.113.7"}, "http": {"path": "/database"}},
                 "path": "/database", "httpMethod": "GET",
                 "headers": {"Authorization": "vams_unknown-key-for-test"}}

        result = core.authenticate_request(event, fronted="none")

        assert result["authorized"] is False
        assert ssm.calls == 0
