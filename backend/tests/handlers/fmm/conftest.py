# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

os.environ["FMM_SCHEMA_STORAGE_TABLE_NAME"] = "test-fmm-schema-table"
os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"] = "test-fmm-compliance-table"
os.environ["FMM_EVALUATION_STORAGE_TABLE_NAME"] = "test-fmm-evaluation-table"
os.environ["FMM_CASCADE_STORAGE_TABLE_NAME"] = "test-fmm-cascade-table"
os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"] = "test-fmm-audit-table"
os.environ["DATABASE_STORAGE_TABLE_NAME"] = "test-database-table"
os.environ["ASSET_STORAGE_TABLE_NAME"] = "test-asset-table"
os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"] = "test-asset-links-table"
os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"] = "test-asset-metadata-table"
os.environ["METADATA_SCHEMA_STORAGE_TABLE_V2_NAME"] = "test-metadata-schema-table"
os.environ["PIPELINE_STORAGE_TABLE_NAME"] = "test-pipeline-table"
os.environ["WORKFLOW_STORAGE_TABLE_NAME"] = "test-workflow-table"
os.environ["S3_ASSET_STORAGE_BUCKET"] = "test-asset-bucket"
os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"] = "test-s3-asset-buckets-table"
os.environ["S3_ASSETAUXILIARY_STORAGE_BUCKET"] = "test-auxiliary-bucket"

# ---------------------------------------------------------------------------
# Build a real module hierarchy for backend.backend.handlers.fmm so that
# unittest.mock.patch() can resolve dotted paths like
# "backend.backend.handlers.fmm.fmmAuditService.audit_table".
#
# The root conftest inserts MockModule instances into sys.modules which
# break patch()'s getattr-based resolution. We replace them with real
# types.ModuleType objects with __path__ pointing to the actual filesystem.
# ---------------------------------------------------------------------------

_backend_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
)

_module_paths = {
    "backend": _backend_root,
    "backend.backend": _backend_root,
    "backend.backend.handlers": os.path.join(_backend_root, "handlers"),
    "backend.backend.handlers.fmm": os.path.join(_backend_root, "handlers", "fmm"),
    "backend.backend.models": os.path.join(_backend_root, "models"),
}

for mod_path, fs_path in _module_paths.items():
    existing = sys.modules.get(mod_path)
    if existing is None or not isinstance(existing, types.ModuleType):
        m = types.ModuleType(mod_path)
        m.__path__ = [fs_path]
        m.__package__ = mod_path
        sys.modules[mod_path] = m
    else:
        if not getattr(existing, "__path__", None):
            existing.__path__ = [fs_path]
    # Wire parent → child attribute
    parts = mod_path.rsplit(".", 1)
    if len(parts) == 2:
        parent = sys.modules.get(parts[0])
        if parent is not None:
            setattr(parent, parts[1], sys.modules[mod_path])

# Provide models.common with the response helpers that FMM handlers import.
# The real models/common.py has heavy dependencies (auditLogging) that aren't
# available in the test mock environment, so we provide lightweight stand-ins.
import json as _json
from typing import Any, Dict, Optional, TypedDict


class _APIGatewayProxyResponseV2(TypedDict):
    isBase64Encoded: bool
    statusCode: int
    headers: Dict[str, str]
    body: str


def _common_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "Cache-Control": "no-cache, no-store"}


def _success(status_code: int = 200, body: Any = None) -> _APIGatewayProxyResponseV2:
    if body is None:
        body = {"message": "Success"}
    return {"isBase64Encoded": False, "statusCode": status_code, "headers": _common_headers(), "body": _json.dumps(body)}


def _validation_error(status_code: int = 400, body: Any = None, event=None) -> _APIGatewayProxyResponseV2:
    if body is None:
        body = {"message": "Validation Error"}
    return {"isBase64Encoded": False, "statusCode": status_code, "headers": _common_headers(), "body": _json.dumps(body)}


def _general_error(status_code: int = 400, body: Any = None, event=None) -> _APIGatewayProxyResponseV2:
    if body is None:
        body = {"message": "Error"}
    return {"isBase64Encoded": False, "statusCode": status_code, "headers": _common_headers(), "body": _json.dumps(body)}


def _authorization_error() -> _APIGatewayProxyResponseV2:
    return {"isBase64Encoded": False, "statusCode": 403, "headers": _common_headers(), "body": _json.dumps({"message": "Not Authorized"})}


def _internal_error(status_code: int = 500, body: Any = None, event=None) -> _APIGatewayProxyResponseV2:
    if body is None:
        body = {"message": "Internal Error"}
    return {"isBase64Encoded": False, "statusCode": status_code, "headers": _common_headers(), "body": _json.dumps(body)}


class _VAMSGeneralErrorResponse(Exception):
    pass


_models_common = types.ModuleType("models.common")
_models_common.APIGatewayProxyResponseV2 = _APIGatewayProxyResponseV2
_models_common.success = _success
_models_common.validation_error = _validation_error
_models_common.general_error = _general_error
_models_common.authorization_error = _authorization_error
_models_common.internal_error = _internal_error
_models_common.VAMSGeneralErrorResponse = _VAMSGeneralErrorResponse
sys.modules["models.common"] = _models_common

# Stub models and models.fmm
for mod_path in ["models", "models.fmm"]:
    if mod_path not in sys.modules:
        m = types.ModuleType(mod_path)
        m.__path__ = []
        m.__package__ = mod_path
        sys.modules[mod_path] = m

# Also wire handlers.fmm so lazy imports like `from handlers.fmm.X import Y` work
_handlers_fs = os.path.join(_backend_root, "handlers")
_fmm_fs = os.path.join(_handlers_fs, "fmm")
for mod_path, fs_path in [
    ("handlers", _handlers_fs),
    ("handlers.fmm", _fmm_fs),
]:
    existing = sys.modules.get(mod_path)
    if existing is None or not isinstance(existing, types.ModuleType):
        m = types.ModuleType(mod_path)
        m.__path__ = [fs_path]
        m.__package__ = mod_path
        sys.modules[mod_path] = m
    else:
        if not getattr(existing, "__path__", None):
            existing.__path__ = [fs_path]

# Ensure handlers.auth and handlers.authz are loaded from mocks (they provide
# request_to_claims and CasbinEnforcer).  The root conftest sets these up in a
# per-test fixture, but our module-level eager imports run before that fixture,
# so we load them here.
_mocks_base = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mocks")
)

if "handlers.auth" not in sys.modules or not hasattr(
    sys.modules.get("handlers.auth"), "request_to_claims"
):
    _auth_spec = importlib.util.spec_from_file_location(
        "handlers.auth", os.path.join(_mocks_base, "handlers", "auth.py")
    )
    _auth_mod = importlib.util.module_from_spec(_auth_spec)
    _auth_spec.loader.exec_module(_auth_mod)
    sys.modules["handlers.auth"] = _auth_mod

if "handlers.authz" not in sys.modules or not hasattr(
    sys.modules.get("handlers.authz"), "CasbinEnforcer"
):
    _authz_spec = importlib.util.spec_from_file_location(
        "handlers.authz", os.path.join(_mocks_base, "handlers", "authz.py")
    )
    _authz_mod = importlib.util.module_from_spec(_authz_spec)
    _authz_spec.loader.exec_module(_authz_mod)
    sys.modules["handlers.authz"] = _authz_mod


def _import_fmm_module(module_name: str):
    """Import a single FMM handler module from the filesystem and register it."""
    full_name = f"backend.backend.handlers.fmm.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    file_path = os.path.join(_fmm_fs, f"{module_name}.py")
    if not os.path.isfile(file_path):
        return None

    spec = importlib.util.spec_from_file_location(full_name, file_path)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    # Also register as short path for internal imports
    short_name = f"handlers.fmm.{module_name}"
    sys.modules[short_name] = mod

    try:
        spec.loader.exec_module(mod)
    except Exception:
        # If import fails, remove from sys.modules
        sys.modules.pop(full_name, None)
        sys.modules.pop(short_name, None)
        return None

    # Set as attribute on parent
    fmm_parent = sys.modules.get("backend.backend.handlers.fmm")
    if fmm_parent is not None:
        setattr(fmm_parent, module_name, mod)
    handlers_fmm = sys.modules.get("handlers.fmm")
    if handlers_fmm is not None:
        setattr(handlers_fmm, module_name, mod)

    return mod


# Eagerly import FMM handler modules so patch() can find their attributes
_fmm_handler_names = [
    "fmmNotifications",
    "fmmAuditService",
    "fmmCascadeExecutor",
    "fmmComplianceTrigger",
    "fmmCascadeService",
    "fmmSchemaService",
    "fmmSchemaBindingService",
    "fmmEvaluateService",
    "fmmEvaluationEngine",
    "fmmPipelineCallback",
    "fmmQuarantineService",
]
for _name in _fmm_handler_names:
    _import_fmm_module(_name)


@pytest.fixture(autouse=True)
def mock_casbin_enforcer():
    """Auto-mock CasbinEnforcer to always allow API access in FMM tests."""
    mock_enforcer_instance = MagicMock()
    mock_enforcer_instance.enforceAPI.return_value = True
    mock_enforcer_instance.enforce.return_value = True
    mock_enforcer_class = MagicMock(return_value=mock_enforcer_instance)

    patch_targets = [
        "backend.backend.handlers.fmm.fmmSchemaService.CasbinEnforcer",
        "backend.backend.handlers.fmm.fmmEvaluateService.CasbinEnforcer",
        "backend.backend.handlers.fmm.fmmQuarantineService.CasbinEnforcer",
        "backend.backend.handlers.fmm.fmmCascadeService.CasbinEnforcer",
        "backend.backend.handlers.fmm.fmmAuditService.CasbinEnforcer",
        "backend.backend.handlers.fmm.fmmSchemaBindingService.CasbinEnforcer",
    ]

    active_patches = []
    for target in patch_targets:
        try:
            p = patch(target, mock_enforcer_class)
            p.start()
            active_patches.append(p)
        except (ModuleNotFoundError, ImportError, AttributeError):
            pass

    yield mock_enforcer_instance

    for p in active_patches:
        p.stop()
