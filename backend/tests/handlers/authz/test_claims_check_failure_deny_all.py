# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A request whose custom claims check raised is refused on every Casbin check.

``request_to_claims`` marks such claims ``CLAIMS_CHECK_FAILED``. A ``CasbinEnforcer`` built from them
decides on a deny-all service of its own: Tier 1, Tier 2 and the direct ``service_object.enforce``
callers are refused, the user's policy is not read, and the per-user caches are left alone, so the
user's next request is decided on their own policy. ``enforceAPI`` also refuses when the claims it
reads for the event carry the mark. The chain from a raising hook to the refusal is driven with the
real ``request_to_claims``.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz
from backend.backend.handlers.authz import CasbinEnforcer, CasbinEnforcerService

USER_ID = "hook-user"
MARKED = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True, "claimsCheckFailed": True}
UNMARKED = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True}

OBJECTS = [
    {"object__type": "asset", "databaseId": "db1", "assetId": "a1"},
    {"object__type": "database", "databaseId": "db1"},
    {"object__type": "api", "route__path": "/database/db1/assets/a1"},
]
ACTIONS = ["GET", "PUT", "POST", "DELETE"]


def _api_event(method="GET", path="/database/db1/assets/a1/download"):
    return {"headers": {}, "requestContext": {"requestId": "req-1", "http": {"method": method, "path": path},
                                              "authorizer": {"lambda": {}}}}


def _allow_all_service():
    """A cached service that allows everything, standing in for the user's own policy."""
    return SimpleNamespace(enforce=lambda obj, act: True, _dateTime_Cached=datetime.now(), _mfaEnabled=True)


@pytest.fixture
def isolated(monkeypatch):
    """Fresh per-user caches, no audit writes, and a policy build that would be visible if it ran."""
    user_enforcers, user_policies = {}, {}
    monkeypatch.setattr(authz, "casbin_user_enforcer_map", user_enforcers)
    monkeypatch.setattr(authz, "casbin_user_policy_map", user_policies)
    monkeypatch.setattr(authz, "log_authorization", MagicMock())
    monkeypatch.setattr(authz, "log_authorization_api", MagicMock())
    create_policy = MagicMock(return_value=authz.POLICY_TEXT_DENY_ALL)
    monkeypatch.setattr(CasbinEnforcerService, "_create_policy_text", create_policy)
    return SimpleNamespace(enforcers=user_enforcers, policies=user_policies, create_policy=create_policy)


@pytest.mark.unit
class TestDenyAllService:
    def test_marked_claims_are_refused_on_every_check(self, isolated, monkeypatch):
        monkeypatch.setattr(authz, "request_to_claims", lambda event: dict(MARKED))
        enforcer = CasbinEnforcer(dict(MARKED))

        decisions = [enforcer.enforce(obj, act) for obj in OBJECTS for act in ACTIONS]
        decisions += [enforcer.service_object.enforce(obj, act) for obj in OBJECTS for act in ACTIONS]
        decisions += [enforcer.enforceAPI(_api_event(act), act) for act in ACTIONS]
        assert decisions and not any(decisions)

    def test_the_users_policy_is_not_read_and_nothing_is_cached(self, isolated):
        enforcer = CasbinEnforcer(dict(MARKED))

        assert enforcer.service_object._enforcer_policy_text == authz.POLICY_TEXT_DENY_ALL
        isolated.create_policy.assert_not_called()
        assert USER_ID not in isolated.enforcers
        assert USER_ID not in isolated.policies

    def test_a_cached_allow_for_the_user_is_neither_used_nor_replaced(self, isolated, monkeypatch):
        monkeypatch.setattr(authz, "request_to_claims", lambda event: dict(UNMARKED))
        cached = _allow_all_service()
        isolated.enforcers[USER_ID] = cached

        assert CasbinEnforcer(dict(MARKED)).enforce(OBJECTS[0], "GET") is False
        assert isolated.enforcers[USER_ID] is cached
        # Positive control: the next request, with claims unmarked, is decided on the cached policy.
        assert CasbinEnforcer(dict(UNMARKED)).enforce(OBJECTS[0], "GET") is True


@pytest.mark.unit
class TestEnforceApiReadsTheEventClaims:
    def test_marked_event_claims_refuse_the_route(self, isolated, monkeypatch):
        isolated.enforcers[USER_ID] = _allow_all_service()
        enforcer = CasbinEnforcer(dict(UNMARKED))

        monkeypatch.setattr(authz, "request_to_claims", lambda event: dict(UNMARKED))
        assert enforcer.enforceAPI(_api_event()) is True  # positive control
        monkeypatch.setattr(authz, "request_to_claims", lambda event: dict(MARKED))
        assert enforcer.enforceAPI(_api_event()) is False


def _real_auth_module():
    """The production handlers/auth/__init__.py, loaded by path under its own name: this directory's
    conftest replaces ``handlers.auth`` with a stub, and the repo-wide one mocks the package name."""
    import importlib.util
    import os

    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend",
                                        "handlers", "auth", "__init__.py"))
    spec = importlib.util.spec_from_file_location("vams_real_handlers_auth_for_claims_check_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_a_raising_claims_hook_refuses_the_request(isolated, monkeypatch):
    """The real request_to_claims, with the customer hook raising, feeds the real enforcer."""
    auth = _real_auth_module()
    assert auth.CLAIMS_CHECK_FAILED == authz.CLAIMS_CHECK_FAILED

    event = _api_event()
    event["requestContext"]["authorizer"] = {"lambda": {
        "vams:tokens": json.dumps([USER_ID]), "vams:roles": json.dumps(["admin"]), "vams:mfaEnabled": "true",
    }}
    isolated.enforcers[USER_ID] = _allow_all_service()

    with patch.object(auth, "customAuthClaimsCheckOverride", side_effect=RuntimeError("hook down")):
        claims = auth.request_to_claims(event)
        monkeypatch.setattr(authz, "request_to_claims", auth.request_to_claims)
        enforcer = CasbinEnforcer(claims)
        assert enforcer.enforceAPI(event) is False
        assert enforcer.enforce(OBJECTS[0], "GET") is False

    # Positive control: the same event with a hook that returns is decided on the cached allow.
    with patch.object(auth, "customAuthClaimsCheckOverride", side_effect=lambda c, r: c):
        claims = auth.request_to_claims(event)
        assert CasbinEnforcer(claims).enforceAPI(event) is True
