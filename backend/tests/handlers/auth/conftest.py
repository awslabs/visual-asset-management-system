# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import sys
import types

import pytest

# The repo-wide tests/conftest.py installs a bare mock common.constants that omits
# the constraint object-type mapping the auth constraints handler binds at import
# time. constants.py is pure data with no AWS dependencies, so pin the real module
# (single source of truth) at collection time, before the auth test modules import
# their handlers. Directory conftests load after the parent, so this wins.
_BACKEND_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))


def _install_real_constants():
    common_pkg = sys.modules.get("common")
    if not isinstance(common_pkg, types.ModuleType) or not hasattr(common_pkg, "__path__"):
        common_pkg = types.ModuleType("common")
        common_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "common")]
        sys.modules["common"] = common_pkg
    spec = importlib.util.spec_from_file_location(
        "common.constants", os.path.join(_BACKEND_PKG_DIR, "common", "constants.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["common.constants"] = module
    spec.loader.exec_module(module)


_install_real_constants()

# Several auth handlers load required environment variables at module import time
# (gold-standard pattern: raise on missing). Handler modules are imported during
# collection, before any fixture runs, so these must be set at conftest load time.
os.environ.setdefault("USER_STORAGE_TABLE_NAME", "test-user-table")
os.environ.setdefault("COGNITO_AUTH_ENABLED", "true")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AUTH_MODE", "cognito")
os.environ.setdefault("API_FRONTED", "none")
os.environ.setdefault("USER_POOL_ID", "test-pool-id")
os.environ.setdefault("APP_CLIENT_ID", "test-client-id")
os.environ.setdefault("COGNITO_BASE_URL", "https://cognito-idp.us-east-1.amazonaws.com")
os.environ.setdefault("JWT_ISSUER_URL", "https://test.issuer.com")
os.environ.setdefault("JWT_AUDIENCE", "test-audience")
os.environ.setdefault("ALLOWED_IP_RANGES", "[]")
os.environ.setdefault("IGNORED_PATHS", "[]")
os.environ.setdefault("API_KEY_STORAGE_TABLE_NAME", "test-apikey-table")
os.environ.setdefault("USER_ROLES_STORAGE_TABLE_NAME", "test-userroles-table")


@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["USER_STORAGE_TABLE_NAME"] = "test-user-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ["AUTH_MODE"] = "cognito"
    os.environ["API_FRONTED"] = "none"
    os.environ["USER_POOL_ID"] = "test-pool-id"
    os.environ["APP_CLIENT_ID"] = "test-client-id"
    os.environ["COGNITO_BASE_URL"] = "https://cognito-idp.us-east-1.amazonaws.com"
    os.environ["JWT_ISSUER_URL"] = "https://test.issuer.com"
    os.environ["JWT_AUDIENCE"] = "test-audience"
    os.environ["ALLOWED_IP_RANGES"] = "[]"
    os.environ["IGNORED_PATHS"] = "[]"
    os.environ["API_KEY_STORAGE_TABLE_NAME"] = "test-apikey-table"
    os.environ["USER_ROLES_STORAGE_TABLE_NAME"] = "test-userroles-table"
