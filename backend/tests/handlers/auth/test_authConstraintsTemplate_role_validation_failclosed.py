# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Role validation on the permission-template import path must not report "cannot check" as "exists".

A template import names one role and every constraint it writes is bound to it. The role check is
deliberately advisory here -- the import proceeds either way -- so its whole value is the operator
warning that the constraints will not be effective. A check that answers True when the roles lookup
is throttled, denied, or unavailable suppresses exactly that warning, and the operator is left
believing a role-bound (possibly DENY) constraint took effect.

Two ways the answer could be manufactured rather than looked up: the lookup raising, and the roles
table not being resolvable at all. The second is unreachable in production because module load now
fails when the table name cannot resolve; it is asserted anyway so the fail-open is not
reintroduced as a defensive guard.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "auth", "authConstraintsTemplateService.py")


def _roles_table_returning(item=None):
    table = MagicMock()
    table.get_item.return_value = {'Item': item} if item is not None else {}
    return table


def _exploding_roles_table():
    table = MagicMock()
    table.get_item.side_effect = Exception("ProvisionedThroughputExceeded")
    return table


def _load_module_with(get_table_name):
    """Execute the module from its own file with a substitute name resolver.

    Returns the loaded module; propagates whatever module load raises. The authz/auth
    packages are stubbed because the root conftest replaces them with mocks that do not
    carry the symbols this module imports by name.
    """
    resource_names = sys.modules['common.resourceNames']
    stub_names = ("handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    spec = importlib.util.spec_from_file_location(
        "authConstraintsTemplateService_load_probe", os.path.abspath(_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    try:
        with patch.object(resource_names, 'get_table_name', get_table_name), \
                patch("boto3.resource", return_value=MagicMock()), \
                patch("boto3.client", return_value=MagicMock()):
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module


@pytest.mark.unit
class TestRoleValidationFailsClosed:
    def test_an_existing_role_is_reported_present(self, monkeypatch):
        """POSITIVE CONTROL: the class below is satisfied by a check that always answers False."""
        monkeypatch.setattr(svc, 'roles_table', _roles_table_returning({'roleName': 'r1'}))

        assert svc.validate_constraint_role_exists('r1') is True

    def test_an_absent_role_is_reported_missing(self, monkeypatch):
        monkeypatch.setattr(svc, 'roles_table', _roles_table_returning(None))

        assert svc.validate_constraint_role_exists('ghost-role') is False

    def test_a_failed_lookup_is_not_reported_as_present(self, monkeypatch):
        table = _exploding_roles_table()
        monkeypatch.setattr(svc, 'roles_table', table)

        assert svc.validate_constraint_role_exists('r1') is False
        # The KEY is the claim, not the call count: a retry or a safety re-read is a safe change.
        assert table.get_item.called, "the roles table was never read"
        table.get_item.assert_any_call(Key={'roleName': 'r1'})

    def test_an_unresolvable_roles_table_is_not_reported_as_present(self, monkeypatch):
        monkeypatch.setattr(svc, 'roles_table', None)

        assert svc.validate_constraint_role_exists('r1') is False


@pytest.mark.unit
class TestTheImporterWarnsWhenTheRoleCannotBeConfirmed:
    """The advisory warning is the only operator-facing signal, so it must survive a bad lookup."""

    # The advisory the operator needs. Matched on its own wording rather than on the role name,
    # because the fail-open logged the role name too -- in a line that said the opposite.
    _ADVISORY = 'may not be effective'

    @staticmethod
    def _import_with(roles_table, monkeypatch):
        monkeypatch.setattr(svc, 'roles_table', roles_table)
        monkeypatch.setattr(svc, 'constraints_table', MagicMock())
        recorder = MagicMock()
        monkeypatch.setattr(svc, 'logger', recorder)
        response = svc.import_template_constraints(
            {'variableValues': {'ROLE_NAME': 'ghost-role'}, 'constraints': [],
             'template': {'name': 'probe'}},
            {"tokens": ["u1"]})
        warnings = " ".join(str(call) for call in recorder.warning.call_args_list)
        return response, warnings

    def test_a_failed_lookup_still_warns(self, monkeypatch):
        response, warnings = self._import_with(_exploding_roles_table(), monkeypatch)

        assert response.success is True
        assert self._ADVISORY in warnings, warnings
        assert 'ghost-role' in warnings, warnings

    def test_an_absent_role_still_warns(self, monkeypatch):
        response, warnings = self._import_with(_roles_table_returning(None), monkeypatch)

        assert response.success is True
        assert self._ADVISORY in warnings, warnings
        assert 'ghost-role' in warnings, warnings

    def test_a_confirmed_role_does_not_warn_about_the_role(self, monkeypatch):
        """POSITIVE CONTROL: the advisory is conditional, not emitted unconditionally."""
        response, warnings = self._import_with(
            _roles_table_returning({'roleName': 'ghost-role'}), monkeypatch)

        assert response.success is True
        assert self._ADVISORY not in warnings, warnings


@pytest.mark.unit
class TestModuleLoadDoesNotDegradeToAnUnusableTable:
    """A name that cannot resolve must fail the cold start, not leave a None table behind for the
    role check to interpret as "skip validation"."""

    def test_load_raises_when_the_roles_table_name_cannot_resolve(self):
        asked = []

        def resolver(key):
            param_key = getattr(key, 'param_key', key)
            asked.append(param_key)
            if param_key == svc.ResourceKeys.ROLES_STORAGE_TABLE.param_key:
                raise Exception("SSM parameter not found")
            return "resolved-table"

        with pytest.raises(Exception):
            _load_module_with(resolver)

        # Without this the assertion above is satisfied by a load that failed for some other
        # reason, or by a resolver that was never consulted.
        assert svc.ResourceKeys.ROLES_STORAGE_TABLE.param_key in asked, asked

    def test_load_succeeds_when_both_names_resolve(self):
        """POSITIVE CONTROL: the raise above is the name resolution, not an unrelated import
        failure in the probe loader."""
        module = _load_module_with(lambda key: "resolved-table")

        assert module.roles_table is not None
        assert module.constraints_table is not None
