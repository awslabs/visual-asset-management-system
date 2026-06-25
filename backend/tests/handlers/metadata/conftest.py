# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Directory-level conftest for the metadata service tests.

The consolidated metadata handler (handlers.metadata.metadataService) imports several
real `common.*` submodules at load time — notably `common.metadataSchemaValidation`,
`common.constants`, and `common.validators`. The repo-wide tests/conftest.py installs a
bare MagicMock for `common` at import time, so those submodule imports fail collection
with "No module named 'common.metadataSchemaValidation'; 'common' is not a package".

This conftest (directory conftests load after the parent) repairs sys.modules for this
directory by loading the REAL `common` package and its submodules by file path, plus a
working customLogging package, and sets the region/env the handler needs for its
module-level boto3 clients. Mirrors tests/handlers/authz/conftest.py.
"""

import importlib.util
import os
import sys
import types

# Region + tables needed before importing the handler (module-level boto3 clients).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

_THIS_DIR = os.path.dirname(__file__)
_BACKEND_PKG_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "..", "backend"))
_MOCKS_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "mocks"))


def _load_module_from_path(module_name, file_path, is_package=False):
    submodule_locations = [os.path.dirname(file_path)] if is_package else None
    spec = importlib.util.spec_from_file_location(
        module_name, file_path, submodule_search_locations=submodule_locations
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_real_common_and_logging():
    # Real `common` package + the submodules the metadata handler imports.
    common_pkg = types.ModuleType("common")
    common_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "common")]
    sys.modules["common"] = common_pkg
    for sub in ("constants", "validators", "metadataSchemaValidation", "dynamodb"):
        _load_module_from_path(
            f"common.{sub}", os.path.join(_BACKEND_PKG_DIR, "common", f"{sub}.py")
        )

    # Working customLogging package (mocks: no CloudWatch/boto needed).
    _load_module_from_path(
        "customLogging", os.path.join(_MOCKS_DIR, "customLogging", "__init__.py"), is_package=True
    )
    _load_module_from_path(
        "customLogging.logger", os.path.join(_MOCKS_DIR, "customLogging", "logger.py")
    )
    _load_module_from_path(
        "customLogging.auditLogging", os.path.join(_MOCKS_DIR, "customLogging", "auditLogging.py")
    )

    # handlers.auth.request_to_claims stub (imported at handler load time).
    handlers_pkg = sys.modules.get("handlers")
    if not isinstance(handlers_pkg, types.ModuleType) or not hasattr(handlers_pkg, "__path__"):
        handlers_pkg = types.ModuleType("handlers")
        handlers_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "handlers")]
        sys.modules["handlers"] = handlers_pkg
    auth_mod = types.ModuleType("handlers.auth")
    auth_mod.request_to_claims = lambda event: {"tokens": ["test-user"], "roles": [], "mfaEnabled": False}
    sys.modules["handlers.auth"] = auth_mod


_install_real_common_and_logging()
