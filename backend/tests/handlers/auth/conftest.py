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


@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["USER_STORAGE_TABLE_NAME"] = "test-user-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    # Add any other required environment variables here
