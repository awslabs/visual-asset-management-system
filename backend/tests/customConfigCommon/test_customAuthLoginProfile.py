# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Claims extraction in the login-profile customization hook.

``customAuthProfileLoginWriteOverride`` reads the caller's claims to populate the stored
user profile. The REST API (v1) REQUEST authorizer delivers claims as a flat map of string
values under ``requestContext.authorizer``, not nested under ``authorizer.jwt.claims`` or
``authorizer.lambda`` as the HTTP API (v2) did. These tests pin that the hook reads the
deployed REST shape (so the default email override is not silently inert), still reads the
nested shapes for a customized copy carried across the migration, and does not raise on
event shapes that carry no authorizer context.

The module is loaded directly from its file path because it imports VAMS handler packages
that the shared ``conftest.py`` replaces with mocks.
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend", "customConfigCommon", "customAuthLoginProfile.py",
)


_STUBS = {
    "customLogging": {},
    "customLogging.logger": {"safeLogger": MagicMock(return_value=MagicMock())},
    "handlers": {},
    "handlers.auth": {"request_to_claims": MagicMock(return_value={})},
    "handlers.authz": {"CasbinEnforcer": MagicMock()},
    "common": {},
    "common.constants": {"STANDARD_JSON_RESPONSE": {}},
    "requests": {"get": MagicMock()},
}


def _load_hook():
    """Load the hook module with its VAMS imports stubbed out.

    Other test modules (and the shared conftest) may already have registered partial mocks
    for these package names, so the required attributes are ensured on whatever object is
    present rather than only when the name is absent from sys.modules. Prior module state is
    restored afterwards so this does not perturb tests that run later in the session.
    """
    saved = {}
    for name, attrs in _STUBS.items():
        existing = sys.modules.get(name)
        if existing is None:
            sys.modules[name] = MagicMock()
            saved[name] = (False, None)
        else:
            saved[name] = (True, {a: getattr(existing, a, None) for a in attrs})
        for attr, value in attrs.items():
            if not hasattr(sys.modules[name], attr) or getattr(sys.modules[name], attr) is None:
                setattr(sys.modules[name], attr, value)

    try:
        spec = importlib.util.spec_from_file_location("_real_customAuthLoginProfile", _HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, (existed, attrs) in saved.items():
            if not existed:
                sys.modules.pop(name, None)
            elif attrs:
                for attr, value in attrs.items():
                    if value is None:
                        try:
                            delattr(sys.modules[name], attr)
                        except AttributeError:
                            pass
                    else:
                        setattr(sys.modules[name], attr, value)


@pytest.fixture(scope="module")
def hook():
    return _load_hook().customAuthProfileLoginWriteOverride


def _profile():
    return {"userId": "u1", "email": "stored@example.com"}


@pytest.mark.unit
class TestClaimsEmailOverride:
    def test_rest_flat_authorizer_context_overrides_email(self, hook):
        """The deployed REST shape: claims are a flat string map under 'authorizer'."""
        event = {
            "requestContext": {
                "authorizer": {
                    "principalId": "u1",
                    "sub": "u1",
                    "email": "rest@example.com",
                    "vams:tokens": '["u1"]',
                }
            }
        }
        assert hook(_profile(), event)["email"] == "rest@example.com"

    def test_v2_nested_jwt_claims_overrides_email(self, hook):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": {"email": "jwt@example.com"}}}}}
        assert hook(_profile(), event)["email"] == "jwt@example.com"

    def test_v2_nested_lambda_claims_overrides_email(self, hook):
        event = {"requestContext": {"authorizer": {"lambda": {"email": "lam@example.com"}}}}
        assert hook(_profile(), event)["email"] == "lam@example.com"

    def test_user_id_is_never_altered(self, hook):
        """userId is the profile lookup key and must survive the override untouched."""
        event = {"requestContext": {"authorizer": {"sub": "someone-else", "email": "x@example.com"}}}
        assert hook(_profile(), event)["userId"] == "u1"


@pytest.mark.unit
class TestNoClaimsAvailable:
    def test_cross_call_event_keeps_stored_email(self, hook):
        """A cross-call carries no email claim, so the stored value must be preserved."""
        event = {"lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        assert hook(_profile(), event)["email"] == "stored@example.com"

    def test_missing_request_context_does_not_raise(self, hook):
        assert hook(_profile(), {})["email"] == "stored@example.com"

    def test_null_authorizer_does_not_raise(self, hook):
        assert hook(_profile(), {"requestContext": {"authorizer": None}})["email"] == "stored@example.com"

    def test_empty_authorizer_keeps_stored_email(self, hook):
        assert hook(_profile(), {"requestContext": {"authorizer": {}}})["email"] == "stored@example.com"

    def test_blank_email_claim_does_not_overwrite_stored_email(self, hook):
        event = {"requestContext": {"authorizer": {"sub": "u1", "email": ""}}}
        assert hook(_profile(), event)["email"] == "stored@example.com"

    def test_principal_id_alone_is_not_treated_as_an_email_claim(self, hook):
        """principalId is authorizer metadata, not a claim; it must be stripped."""
        event = {"requestContext": {"authorizer": {"principalId": "u1"}}}
        assert hook(_profile(), event)["email"] == "stored@example.com"
