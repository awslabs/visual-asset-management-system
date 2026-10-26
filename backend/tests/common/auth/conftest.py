# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Directory-scoped test setup for the relocated common/auth modules.

The root conftest replaces ``sys.modules['common']`` with a mock package that
has no ``auth`` subpackage, so the real ``common.auth.*`` modules these tests
exercise are not importable by their absolute names. Register them here (real
modules, loaded by path) at collection time, scoped to this directory only so
the broader suite's mock wiring is untouched. clientIp must be registered before
authorizerCore (authorizerCore imports ``from common.auth.clientIp import ...``).
"""
import os
import sys
import importlib.util


def _load(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_auth_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "auth"
)
_auth_dir = os.path.abspath(_auth_dir)

_common_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common"
)
_common_dir = os.path.abspath(_common_dir)

# Package marker first, then dependencies (resourceNames), then pure modules, then authorizerCore (depends on clientIp and resourceNames).
# originVerify precedes clientIp, which imports it.
if "common.auth" not in sys.modules:
    _load("common.auth", os.path.join(_auth_dir, "__init__.py"))
_load("common.resourceNames", os.path.join(_common_dir, "resourceNames.py"))
_load("common.auth.originVerify", os.path.join(_auth_dir, "originVerify.py"))
_load("common.auth.clientIp", os.path.join(_auth_dir, "clientIp.py"))
_load("common.auth.apiEvent", os.path.join(_auth_dir, "apiEvent.py"))
_load("common.auth.authorizerCore", os.path.join(_auth_dir, "authorizerCore.py"))

import pytest  # noqa: E402

# The origin verification value the tests below configure, and the secret ARN that enables it.
ORIGIN_VERIFY_TEST_VALUE = "originVerifyTestValue0123456789"
ORIGIN_VERIFY_TEST_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-origin-verify-AbCdEf"
)


@pytest.fixture
def origin_verify_value(monkeypatch):
    """Configure the origin verification module clientIp uses to accept one known value.

    Patches the module object ``resolve_client_ip`` reads (``clientIp.originVerify``), whichever
    import path loaded clientIp, and returns the value a CloudFront-forwarded request carries in
    ``x-vams-origin-verify``.
    """
    origin_verify = sys.modules["common.auth.originVerify"]
    monkeypatch.setenv(origin_verify.SECRET_ARN_ENV, ORIGIN_VERIFY_TEST_SECRET_ARN)
    monkeypatch.setattr(
        origin_verify, "_fetch_accepted_values", lambda secret_arn: (ORIGIN_VERIFY_TEST_VALUE,)
    )
    monkeypatch.setitem(origin_verify._cache, "values", ())
    monkeypatch.setitem(origin_verify._cache, "fetched_at", None)
    return ORIGIN_VERIFY_TEST_VALUE
