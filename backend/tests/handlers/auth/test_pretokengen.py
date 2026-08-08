# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cognito pre-token-generation triggers.

The triggers add ``vams:tokens`` and ``email`` to the issued token. They deliberately leave
``vams:roles`` (and ``vams:externalAttributes``) empty: roles are resolved at authorization
time by the API Gateway authorizer (``common/auth/authorizerCore.py``), which covers every
auth mode rather than Cognito alone and lets a role change take effect without re-issuing a
token. These tests pin that contract so a role lookup is not reintroduced here.

The modules are loaded directly from their file paths because the shared ``conftest.py``
registers a MagicMock stand-in for ``pretokengenv1`` in ``sys.modules``.
"""
import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

_AUTH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "backend", "handlers", "auth",
)


def _load(module_name):
    """Load a pretokengen module from disk, bypassing the conftest sys.modules mock."""
    path = os.path.join(_AUTH_DIR, f"{module_name}.py")
    # The modules import customLogging.logger; provide it if the real package is absent.
    if "customLogging" not in sys.modules:
        mock_logging = MagicMock()
        mock_logging.safeLogger = MagicMock(return_value=MagicMock())
        sys.modules["customLogging"] = MagicMock()
        sys.modules["customLogging.logger"] = mock_logging
    spec = importlib.util.spec_from_file_location(f"_real_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event():
    return {
        "userName": "u1",
        "request": {"userAttributes": {"email": "u1@example.com"}},
    }


@pytest.mark.unit
class TestPreTokenGenV1:
    def setup_method(self):
        self.mod = _load("pretokengenv1")

    def _claims(self, event=None):
        result = self.mod.lambda_handler(event or _event(), None)
        return result["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"]

    def test_roles_left_empty(self):
        assert json.loads(self._claims()["vams:roles"]) == []

    def test_external_attributes_left_empty(self):
        assert json.loads(self._claims()["vams:externalAttributes"]) == []

    def test_tokens_and_email_populated(self):
        claims = self._claims()
        assert json.loads(claims["vams:tokens"]) == ["u1"]
        assert claims["email"] == "u1@example.com"

    def test_missing_email_defaults_to_empty_string(self):
        claims = self._claims({"userName": "u1", "request": {"userAttributes": {}}})
        assert claims["email"] == ""
        assert json.loads(claims["vams:tokens"]) == ["u1"]

    def test_original_event_fields_preserved(self):
        result = self.mod.lambda_handler(_event(), None)
        assert result["userName"] == "u1"

    def test_no_role_lookup_helpers_remain(self):
        """Roles are resolved in the authorizer; these helpers must not come back here."""
        assert not hasattr(self.mod, "get_vams_roles")
        assert not hasattr(self.mod, "remember_observed_claims")

    def test_module_does_not_touch_dynamodb(self):
        """No table handles at module scope — the trigger performs no DynamoDB reads/writes."""
        assert not hasattr(self.mod, "userRoleTable")
        assert not hasattr(self.mod, "authEntTable")


@pytest.mark.unit
class TestPreTokenGenV2:
    def setup_method(self):
        self.mod = _load("pretokengenv2")

    def _sections(self, event=None):
        result = self.mod.lambda_handler(event or _event(), None)
        details = result["response"]["claimsAndScopeOverrideDetails"]
        return details["idTokenGeneration"], details["accessTokenGeneration"]

    def test_roles_left_empty_in_both_tokens(self):
        id_token, access_token = self._sections()
        assert json.loads(id_token["claimsToAddOrOverride"]["vams:roles"]) == []
        assert json.loads(access_token["claimsToAddOrOverride"]["vams:roles"]) == []

    def test_tokens_and_email_populated_in_both_tokens(self):
        id_token, access_token = self._sections()
        for section in (id_token, access_token):
            claims = section["claimsToAddOrOverride"]
            assert json.loads(claims["vams:tokens"]) == ["u1"]
            assert claims["email"] == "u1@example.com"

    def test_missing_email_defaults_to_empty_string(self):
        id_token, access_token = self._sections(
            {"userName": "u1", "request": {"userAttributes": {}}}
        )
        assert id_token["claimsToAddOrOverride"]["email"] == ""
        assert access_token["claimsToAddOrOverride"]["email"] == ""

    def test_no_role_lookup_helpers_remain(self):
        assert not hasattr(self.mod, "get_vams_roles")
        assert not hasattr(self.mod, "remember_observed_claims")

    def test_module_does_not_touch_dynamodb(self):
        assert not hasattr(self.mod, "userRoleTable")
        assert not hasattr(self.mod, "authEntTable")
