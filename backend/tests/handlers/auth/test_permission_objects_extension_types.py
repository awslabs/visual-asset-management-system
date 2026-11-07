# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /auth/constraints/permissionObjects lists the merged object types.

With every constants lane empty (as shipped) the response body is exactly the one built from the
core lists. With a lane that registers a type, the type is listed after the core types, with its
fields, so the web constraint editor and the CLI (which read this endpoint and hardcode no list)
offer it.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.auth import authConstraintsService
from backend.backend.handlers.auth.authConstraintsService import lambda_handler

_CONSTANTS_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "constants.py"))


def _load_constants(custom_types=None, custom_fields=None):
    lanes = {
        "common.addonConstants": types.ModuleType("common.addonConstants"),
        "custom.customConstants": types.ModuleType("custom.customConstants"),
    }
    for name, prefix in (("common.addonConstants", "ADDON"), ("custom.customConstants", "CUSTOM")):
        module = lanes[name]
        setattr(module, f"{prefix}_PERMISSION_CONSTRAINT_FIELDS", {})
        setattr(module, f"{prefix}_CONSTRAINT_OBJECT_TYPE_FIELDS", {})
        setattr(module, f"{prefix}_UNALLOWED_FILE_EXTENSIONS", [])
        setattr(module, f"{prefix}_UNALLOWED_MIME_TYPES", [])
    lanes["custom.customConstants"].CUSTOM_PERMISSION_CONSTRAINT_FIELDS = dict(custom_fields or {})
    lanes["custom.customConstants"].CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS = dict(custom_types or {})
    saved = {name: sys.modules.get(name) for name in lanes}
    sys.modules.update(lanes)
    try:
        spec = importlib.util.spec_from_file_location("_permission_objects_constants", _CONSTANTS_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


CORE = _load_constants()


def _event():
    return {
        "requestContext": {"http": {"method": "GET", "path": "/auth/constraints/permissionObjects"}},
        "pathParameters": {}, "queryStringParameters": {},
        "headers": {"authorization": "Bearer test-token"},
    }


@patch("backend.backend.handlers.auth.authConstraintsService.request_to_claims")
@patch("backend.backend.handlers.auth.authConstraintsService.CasbinEnforcer")
def _body(mock_casbin, mock_claims):
    mock_claims.return_value = {"tokens": ["u1"], "roles": ["admin"], "mfaEnabled": False}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    mock_casbin.return_value = enforcer
    response = lambda_handler(_event(), {})
    assert response["statusCode"] == 200, response
    body = json.loads(response["body"])
    return body.get("message", body)


def _core_body():
    return {
        "objectTypes": [
            {"label": CORE.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS[t]["label"], "value": t,
             "fields": CORE.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS[t]["fields"]}
            for t in CORE.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES
        ],
        "operators": CORE.CONSTRAINT_OPERATOR_LABELS,
        "permissions": CORE.CONSTRAINT_PERMISSION_LABELS,
        "permissionTypes": CORE.CONSTRAINT_PERMISSION_TYPE_LABELS,
    }


@pytest.mark.unit
class TestPermissionObjectsLanes:
    def test_the_empty_state_body_is_exactly_the_core_body(self, monkeypatch):
        # The endpoint bound to constants built with empty lanes (a private load), so this holds in a
        # fork whose own lane registers types.
        monkeypatch.setattr(authConstraintsService, "ALLOWED_CONSTRAINT_OBJECT_TYPES",
                            CORE.ALLOWED_CONSTRAINT_OBJECT_TYPES)
        monkeypatch.setattr(authConstraintsService, "CONSTRAINT_OBJECT_TYPE_FIELDS",
                            CORE.CONSTRAINT_OBJECT_TYPE_FIELDS)
        assert _body() == _core_body()
        # Control: the core body is the fifteen core types, not an empty listing.
        assert len(_core_body()["objectTypes"]) == 15

    def test_an_extension_type_is_listed_after_the_core_types(self, monkeypatch):
        merged = _load_constants(
            custom_fields={"widgetId": ""},
            custom_types={"widget": {"label": "Widget", "fields": [
                {"label": "Database ID", "value": "databaseId"},
                {"label": "Widget ID", "value": "widgetId"}]}})
        monkeypatch.setattr(authConstraintsService, "ALLOWED_CONSTRAINT_OBJECT_TYPES",
                            merged.ALLOWED_CONSTRAINT_OBJECT_TYPES)
        monkeypatch.setattr(authConstraintsService, "CONSTRAINT_OBJECT_TYPE_FIELDS",
                            merged.CONSTRAINT_OBJECT_TYPE_FIELDS)
        body = _body()
        assert body["objectTypes"][:-1] == _core_body()["objectTypes"]
        assert body["objectTypes"][-1] == {
            "label": "Widget", "value": "widget",
            "fields": [{"label": "Database ID", "value": "databaseId"},
                       {"label": "Widget ID", "value": "widgetId"}],
        }
