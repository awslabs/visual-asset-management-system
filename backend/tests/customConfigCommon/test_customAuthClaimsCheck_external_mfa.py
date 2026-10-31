# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shipped external OAuth branch of ``customMFATokenScopeCheckOverride`` reports no MFA.

With ``COGNITO_AUTH_ENABLED`` unset the hook takes its external identity provider branch,
which ships as ``mfaLoginEnabled = False``. A token carrying the MFA scope therefore still
yields ``False``, so roles marked ``mfaRequired`` do not activate for external OAuth users
until a deployment implements that branch.

The module is loaded directly from its file path because the shared ``conftest.py`` replaces
``customConfigCommon.customAuthClaimsCheck`` with a mock.
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend", "customConfigCommon", "customAuthClaimsCheck.py",
)


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.delenv("COGNITO_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("USER_POOL_ID", raising=False)
    logger_module = MagicMock()
    logger_module.safeLogger = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "customLogging", MagicMock())
    monkeypatch.setitem(sys.modules, "customLogging.logger", logger_module)
    spec = importlib.util.spec_from_file_location("customAuthClaimsCheck_under_test", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mfa_scoped_claims():
    return {"sub": "user-1", "scope": "openid profile mfa", "auth_time": 1700000000}


@pytest.mark.unit
def test_module_takes_the_external_branch_without_cognito(hook):
    """Control: the env default selects the external identity provider branch."""
    assert hook.cognito_auth_enabled == "FALSE"


@pytest.mark.unit
def test_mfa_scoped_external_token_reports_no_mfa(hook):
    event = {"headers": {"authorization": "Bearer token"}}
    assert hook.customMFATokenScopeCheckOverride("user-1", _mfa_scoped_claims(), event) is False


@pytest.mark.unit
def test_token_without_claims_reports_no_mfa(hook):
    assert hook.customMFATokenScopeCheckOverride("user-1", None, {}) is False
