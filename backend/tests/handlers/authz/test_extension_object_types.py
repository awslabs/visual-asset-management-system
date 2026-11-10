# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An extension permission object type grants nothing until a constraint allows it.

A fork or add-on registers an object type (here `widget`, authorized on `databaseId` and
`widgetId`) through the constants lanes. No constraint is seeded for it, so a role whose
constraints cover every CORE object type, as the seeded admin role's do, is still denied on a
widget. Once a constraint on `widget` exists, the enforcer scrubs a widget object to the type's
registered fields and decides on them, so a `widgetId` criterion allows one widget and denies
another. An unregistered type is denied too.

These tests drive the REAL policy-text builder and the REAL enforce() wrapper, with the authz module
pointed at a private copy of constants.py built with the widget lane.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import patch

import pytest
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

import backend.backend.handlers.authz as authz
from backend.backend.handlers.authz import CasbinEnforcerService

_CONSTANTS_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "constants.py"))

USER_ID = "tester"
W1 = "11111111-2222-4333-8444-555555555555"
W2 = "66666666-7777-4888-9999-000000000000"


def _load_widget_constants():
    addon = types.ModuleType("common.addonConstants")
    custom = types.ModuleType("custom.customConstants")
    for module, prefix in ((addon, "ADDON"), (custom, "CUSTOM")):
        setattr(module, f"{prefix}_PERMISSION_CONSTRAINT_FIELDS", {})
        setattr(module, f"{prefix}_CONSTRAINT_OBJECT_TYPE_FIELDS", {})
        setattr(module, f"{prefix}_UNALLOWED_FILE_EXTENSIONS", [])
        setattr(module, f"{prefix}_UNALLOWED_MIME_TYPES", [])
        setattr(module, f"{prefix}_LOG_SENSITIVE_KEYS", [])
    custom.CUSTOM_PERMISSION_CONSTRAINT_FIELDS = {"widgetId": ""}
    custom.CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS = {"widget": {"label": "Widget", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Widget ID", "value": "widgetId"}]}}
    lanes = {"common.addonConstants": addon, "custom.customConstants": custom}
    saved = {name: sys.modules.get(name) for name in lanes}
    sys.modules.update(lanes)
    try:
        spec = importlib.util.spec_from_file_location("_widget_authz_constants", _CONSTANTS_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


WIDGET_CONSTANTS = _load_widget_constants()


@pytest.fixture(autouse=True)
def widget_lane():
    """Point the authz module at constants built with the widget lane."""
    with patch.object(authz, "PERMISSION_CONSTRAINT_FIELDS",
                      WIDGET_CONSTANTS.PERMISSION_CONSTRAINT_FIELDS), \
         patch.object(authz, "ALWAYS_ALLOWED_OBJECT_KEYS", WIDGET_CONSTANTS.ALWAYS_ALLOWED_OBJECT_KEYS), \
         patch.object(authz, "get_constraint_fields_for_object_type",
                      WIDGET_CONSTANTS.get_constraint_fields_for_object_type):
        yield


_ALL_ACTIONS = [{"groupId": "admin", "permission": action, "permissionType": "allow"}
                for action in ("GET", "PUT", "POST", "DELETE")]


def _match_all_constraint(object_type):
    """An allow-everything constraint on one type, shaped like the seeded admin constraints."""
    field = WIDGET_CONSTANTS.get_constraint_fields_for_object_type(object_type)[0]
    return {
        "constraintId": f"admin-{object_type}", "objectType": object_type,
        "criteriaAnd": [], "criteriaOr": [{"field": field, "operator": "contains", "value": ".*"}],
        "groupPermissions": list(_ALL_ACTIONS), "userPermissions": [],
    }


def _core_admin_policies():
    return [_match_all_constraint(t) for t in WIDGET_CONSTANTS.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES]


def _service(policies):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": "admin"}]), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
        policy_text = svc._create_policy_text_helper()
    new_model = model.Model()
    new_model.load_model_from_text(WIDGET_CONSTANTS.PERMISSION_CONSTRAINT_POLICY)
    svc._enforcer = FastEnforcer(model=new_model, adapter=string_adapter.StringAdapter(policy_text),
                                 enable_log=False)
    return svc


def _widget(widget_id, database_id="db1"):
    return {"databaseId": database_id, "widgetId": widget_id, "object__type": "widget"}


@pytest.mark.unit
class TestExtensionTypeIsNotAutoGranted:
    def test_a_role_covering_every_core_type_is_denied_on_an_extension_type(self):
        svc = _service(_core_admin_policies())
        for action in ("GET", "PUT", "POST", "DELETE"):
            assert svc.enforce(_widget(W1), action) is False

    def test_control_the_same_role_is_allowed_on_every_core_type(self):
        svc = _service(_core_admin_policies())
        assert svc.enforce({"databaseId": "db1", "object__type": "database"}, "GET") is True
        assert svc.enforce({"databaseId": "db1", "assetName": "a", "assetType": ".glb", "tags": [],
                            "object__type": "asset"}, "DELETE") is True
        assert svc.enforce({"route__path": "/custom/database/db1/widgets", "object__type": "api"},
                           "GET") is True

    def test_the_core_admin_set_has_one_constraint_per_core_type(self):
        # Control: the role above really covers all eleven core types, and nothing else.
        types_ = [p["objectType"] for p in _core_admin_policies()]
        assert types_ == WIDGET_CONSTANTS.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES and len(types_) == 11
        assert "widget" in WIDGET_CONSTANTS.ALLOWED_CONSTRAINT_OBJECT_TYPES

    def test_an_unregistered_type_is_denied(self):
        svc = _service(_core_admin_policies() + [_match_all_constraint("widget")])
        assert svc.enforce({"databaseId": "db1", "gizmoId": "g1", "object__type": "gizmo"}, "GET") is False


@pytest.mark.unit
class TestExtensionTypeConstraints:
    def _widget_constraint(self, widget_id):
        return {
            "constraintId": "widget-one", "objectType": "widget",
            "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": "db1"},
                            {"field": "widgetId", "operator": "equals", "value": widget_id}],
            "criteriaOr": [], "userPermissions": [],
            "groupPermissions": [{"groupId": "admin", "permission": "GET", "permissionType": "allow"}],
        }

    def test_a_widget_id_constraint_allows_one_widget_and_denies_another(self):
        svc = _service([self._widget_constraint(W1)])
        assert svc.enforce(_widget(W1), "GET") is True
        assert svc.enforce(_widget(W2), "GET") is False
        # Only the granted action, and only in the granted database.
        assert svc.enforce(_widget(W1), "POST") is False
        assert svc.enforce(_widget(W1, database_id="db2"), "GET") is False

    def test_the_rule_reads_widget_id_because_the_type_registers_it(self):
        """Without the lane, `widgetId` is not a constraint field: the generator drops the
        criterion, so the rule no longer pins the widget. The lane is what makes the attribute an
        authorization attribute."""
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        criterion = [{"field": "widgetId", "operator": "equals", "value": W1}]
        assert svc._generate_criteria_object_rules(criterion, object_type="widget")
        core_constants = WIDGET_CONSTANTS.CORE_PERMISSION_CONSTRAINT_FIELDS
        with patch.object(authz, "PERMISSION_CONSTRAINT_FIELDS", core_constants):
            assert svc._generate_criteria_object_rules(criterion, object_type="widget") == []

    def test_the_scrub_keeps_exactly_the_widget_fields(self):
        """The enforcer scrubs a widget object to the type's registered fields (plus the type gate),
        as it does a core object, so a stray attribute never reaches a rule."""
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        smuggled = dict(_widget(W1), assetName="x", route__path="/x", tags=["t"])
        assert svc._scrub_object_fields(smuggled) == _widget(W1)
