# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The set-level userRole object, evaluated by the REAL Casbin enforcer.

`authorize_user_role_set_operation` evaluates a userRole object carrying only the target
`userId`, so that the verdict on a whole-set write (PUT / DELETE) never depends on what the
target user currently holds. The object therefore has no `roleName`, and
`CasbinEnforcerService.enforce` fills the missing field with the empty placeholder from
`PERMISSION_CONSTRAINT_FIELDS`. Whether an existing constraint still matches that object is a
property of the generated regex, not of the handler, so it is measured here against the real
policy generator and the real enforcer rather than against a stand-in whose verdict the test
chooses.

POST does not use this object -- it can only create the rows it names, so it authorizes those
rows directly. The refusal pinned in `TestRoleScopedConstraintDoesNotReachTheOperation` is
therefore about the whole-set operations only; the assignments such a caller CAN still grant are
measured in test_userRolesService_post_scoped_grant.py.

Two constraints are exercised:

* the **seeded administrator** constraint, `roleName contains '.*'` with GET/PUT/POST/DELETE
  allow (infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin-construct.ts,
  `initial_admin_allow_all_userroles`). Its generated rule is
  `regexMatch(r.obj.roleName, '(?s:.*).*(?s:.*)')`, which matches the empty string, so every
  administrative user-role write keeps working. This is the regression control for the added
  check: an administrator refused here could not manage user roles at all.
* a **role-scoped** constraint, `roleName equals 'viewer'`, whose rule is
  `regexMatch(r.obj.roleName, '^viewer\\Z')` and does not match the empty string. Such a caller
  is refused the whole-role-set operations, which is the deliberate fail-closed direction: they
  may be scoped to individual assignments, not to a user's role set as a whole. Pinned so the
  behaviour is a stated decision rather than an accident of the placeholder value.
"""

import importlib.util
import os

import pytest
from unittest.mock import patch
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.handlers.authz import CasbinEnforcerService
import backend.backend.handlers.authz as authz


def _real_constants():
    """The genuine common/constants.py, loaded by path under a private module name.

    The repo-wide conftest installs a bare MagicMock for `common` outside the authz test
    directory, so `handlers/authz/__init__.py` imported from here holds MagicMocks in place of
    the constraint field list and the field matrix -- with which the real generator emits no
    rules at all and every verdict is a vacuous False. Loading the real module under a private
    name repairs those three references (below) without disturbing `sys.modules`, which the
    other tests in this directory rely on.
    """
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "constants.py"
    )
    spec = importlib.util.spec_from_file_location("_real_common_constants", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSTANTS = _real_constants()
PERMISSION_CONSTRAINT_FIELDS = CONSTANTS.PERMISSION_CONSTRAINT_FIELDS
PERMISSION_CONSTRAINT_POLICY = CONSTANTS.PERMISSION_CONSTRAINT_POLICY


@pytest.fixture(autouse=True)
def real_constraint_constants():
    """Point the authz module at the real constants for the duration of each test."""
    with patch.object(
        authz, "PERMISSION_CONSTRAINT_FIELDS", CONSTANTS.PERMISSION_CONSTRAINT_FIELDS
    ), patch.object(
        authz, "ALWAYS_ALLOWED_OBJECT_KEYS", CONSTANTS.ALWAYS_ALLOWED_OBJECT_KEYS
    ), patch.object(
        authz,
        "get_constraint_fields_for_object_type",
        CONSTANTS.get_constraint_fields_for_object_type,
    ):
        yield


@pytest.mark.unit
def test_the_constants_repair_is_real_and_necessary():
    """Control for the fixture: without it the generator emits nothing and proves nothing."""
    assert "object__type" in PERMISSION_CONSTRAINT_FIELDS
    assert CONSTANTS.get_constraint_fields_for_object_type("userRole") == [
        "roleName",
        "userId",
    ]
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    rules = svc._generate_criteria_object_rules(
        [{"field": "roleName", "operator": "contains", "value": ".*"}],
        object_type="userRole",
    )
    assert rules, (
        "the real rule generator produced no rule for a userRole criterion; the constants "
        "repair is not in effect and every verdict below would be vacuous"
    )


_USER = "admin-user"
_ROLE = "r1"
_TARGET = "target-user"

_ACTIONS = ["GET", "PUT", "POST", "DELETE"]

#: The actions the handler actually evaluates against the set-level object. POST is absent by
#: design (see the module docstring); it is still exercised below because the regex property
#: being measured -- whether a criterion matches the empty roleName placeholder -- is per-action
#: and a future set-level check on any action would have to satisfy the same property.
_SET_LEVEL_ACTIONS = ["PUT", "DELETE"]


def _user_role_policy(criterion, permissions=_ACTIONS):
    return {
        "constraintId": "c1",
        "objectType": "userRole",
        "criteriaAnd": [criterion],
        "criteriaOr": [],
        "groupPermissions": [
            {"groupId": _ROLE, "permission": permission, "permissionType": "allow"}
            for permission in permissions
        ],
        "userPermissions": [],
    }


def _enforcer(policies):
    """The real policy text generator plus the real Casbin enforcer, DynamoDB reads stubbed."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = _USER
    svc._mfaEnabled = True
    user_roles = [{"userId": _USER, "roleName": _ROLE}]
    with patch.object(
        CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles
    ), patch.object(
        CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies
    ):
        policy_text = svc._create_policy_text_helper()

    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    adapter = string_adapter.StringAdapter(policy_text)
    svc._enforcer = FastEnforcer(model=new_model, adapter=adapter, enable_log=False)
    return svc


_SEEDED_ADMIN_CRITERION = {
    "field": "roleName",
    "operator": "contains",
    "value": ".*",
}

_ROLE_SCOPED_CRITERION = {
    "field": "roleName",
    "operator": "equals",
    "value": "viewer",
}


@pytest.mark.unit
class TestSeededAdminConstraintStillMatchesTheOperationObject:
    """Regression control: the added operation-level check must not lock administrators out."""

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_the_operation_object_is_allowed(self, action):
        svc = _enforcer([_user_role_policy(_SEEDED_ADMIN_CRITERION)])
        assert svc.enforce({"userId": _TARGET, "object__type": "userRole"}, action) is True, (
            f"the seeded administrator constraint no longer matches a userRole object with no "
            f"roleName, so {action} on a user's role set is refused for every administrator"
        )

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_a_single_assignment_is_still_allowed(self, action):
        """Control: the per-row checks the operation check sits in front of still pass."""
        svc = _enforcer([_user_role_policy(_SEEDED_ADMIN_CRITERION)])
        assert svc.enforce(
            {"userId": _TARGET, "roleName": "some-role", "object__type": "userRole"}, action
        ) is True

    def test_an_unrelated_object_type_is_still_refused(self):
        """Control: the fixture is not a policy that allows everything."""
        svc = _enforcer([_user_role_policy(_SEEDED_ADMIN_CRITERION)])
        assert svc.enforce({"databaseId": "db1", "object__type": "asset"}, "GET") is False


@pytest.mark.unit
class TestRoleScopedConstraintDoesNotReachTheOperation:
    """A constraint naming specific roles governs assignments, not the whole role set."""

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_the_operation_object_is_refused(self, action):
        svc = _enforcer([_user_role_policy(_ROLE_SCOPED_CRITERION)])
        assert svc.enforce({"userId": _TARGET, "object__type": "userRole"}, action) is False, (
            f"a constraint scoped to one role name matched a userRole object with no roleName, "
            f"so {action} on a user's whole role set is permitted by a role-scoped grant"
        )

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_the_scoped_assignment_is_still_allowed(self, action):
        """Control: the scoped grant itself is live, so the refusal above is about the object."""
        svc = _enforcer([_user_role_policy(_ROLE_SCOPED_CRITERION)])
        assert svc.enforce(
            {"userId": _TARGET, "roleName": "viewer", "object__type": "userRole"}, action
        ) is True


@pytest.mark.unit
class TestThePlaceholderIsWhatTheEnforcerSees:
    """Why the tests above are about an empty roleName: that is what the missing field becomes."""

    def test_role_name_defaults_to_the_empty_string(self):
        assert PERMISSION_CONSTRAINT_FIELDS["roleName"] == ""
