# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Directory-level conftest for the authz (Casbin) tests.

The authz handler is one of the few modules that must be exercised against its
REAL dependencies: the genuine Casbin policy/model and constraint field list from
common.constants, plus a working customLogging package. The repo-wide
tests/conftest.py installs bare MagicMocks for `customLogging`, `common`, and
`common.constants` at import time (and never registers the `customLogging.auditLogging`
submodule that handlers began importing in a recent release). Those mocks poison
collection of these tests with:

    ModuleNotFoundError: No module named 'customLogging.auditLogging'

and would also strip PERMISSION_CONSTRAINT_POLICY / PERMISSION_CONSTRAINT_FIELDS
down to MagicMocks, making a real authorization test impossible.

This conftest runs at collection time (directory conftests load after the parent,
so we win) and repairs sys.modules for this directory:

  * customLogging            -> real package (with .logger and .auditLogging submodules)
  * common / common.constants -> the REAL modules, so the Casbin policy is genuine
  * handlers.auth.request_to_claims -> lightweight stub (not needed by these tests)

Everything is loaded by file path so it is independent of sys.path ordering.
"""

import importlib.util
import os
import sys
import types

# The authz handler creates a module-level boto3 client at import time, which needs
# a region and the constraint table env vars. Set safe defaults before that import.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("USER_ROLES_TABLE_NAME", "userRolesTable")
os.environ.setdefault("ROLES_TABLE_NAME", "rolesTable")
os.environ.setdefault("CONSTRAINTS_TABLE_NAME", "constraintTable")

_THIS_DIR = os.path.dirname(__file__)
# .../backend  (the inner package dir that holds common/, handlers/, customLogging/)
_BACKEND_PKG_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "..", "backend"))
_MOCKS_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "mocks"))


def _load_module_from_path(module_name, file_path, is_package=False):
    """Load a module from an explicit file path and register it in sys.modules.

    Registering before exec lets intra-package imports resolve.
    """
    submodule_locations = [os.path.dirname(file_path)] if is_package else None
    spec = importlib.util.spec_from_file_location(
        module_name, file_path, submodule_search_locations=submodule_locations
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_real_authz_dependencies():
    # --- Real common + common.constants (genuine Casbin policy & field list) ---
    common_pkg = types.ModuleType("common")
    common_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "common")]
    sys.modules["common"] = common_pkg
    _load_module_from_path(
        "common.constants", os.path.join(_BACKEND_PKG_DIR, "common", "constants.py")
    )

    # --- Proper customLogging package with logger + auditLogging submodules ---
    # Use the test mocks (no CloudWatch / boto needed). The mock package now
    # provides both logger.safeLogger and the auditLogging no-ops.
    _load_module_from_path(
        "customLogging",
        os.path.join(_MOCKS_DIR, "customLogging", "__init__.py"),
        is_package=True,
    )
    _load_module_from_path(
        "customLogging.logger", os.path.join(_MOCKS_DIR, "customLogging", "logger.py")
    )
    _load_module_from_path(
        "customLogging.auditLogging",
        os.path.join(_MOCKS_DIR, "customLogging", "auditLogging.py"),
    )

    # --- handlers.auth.request_to_claims stub (authz imports it at module load) ---
    handlers_pkg = sys.modules.get("handlers")
    if not isinstance(handlers_pkg, types.ModuleType) or not hasattr(handlers_pkg, "__path__"):
        handlers_pkg = types.ModuleType("handlers")
        handlers_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "handlers")]
        sys.modules["handlers"] = handlers_pkg
    auth_mod = types.ModuleType("handlers.auth")
    auth_mod.request_to_claims = lambda event: {"tokens": ["test-user"], "roles": [], "mfaEnabled": False}
    sys.modules["handlers.auth"] = auth_mod


_install_real_authz_dependencies()
