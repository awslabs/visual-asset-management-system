# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST authorizes the rows it names, and nothing else -- both halves of that decision.

A set-level userRole object carries only `userId`; `CasbinEnforcerService.enforce` fills the
missing `roleName` with the empty placeholder from `PERMISSION_CONSTRAINT_FIELDS`, and a
constraint scoped to a specific role name generates `regexMatch(r.obj.roleName, '^viewer\\Z')`,
which does not match the empty string. So a set-level check on POST refuses every
roleName-scoped caller unconditionally -- including for the one assignment they are scoped to.

That check closed nothing. POST can only create the rows it names, and
`CreateUserRolesRequestModel.roleName` carries `min_items=1`, so the per-named-row loop always
evaluates at least one object no matter what the target holds. It was removed, and this file
holds both halves of the result:

* **CAPABILITY** -- a roleName-scoped constraint can grant the assignment it is scoped to, and
  still cannot grant any other. Measured against the REAL policy-text generator and the REAL
  Casbin enforcer, because the question is what the generated regex matches, not what a stand-in
  chooses to answer.
* **THE ORACLE STAYS CLOSED** -- a caller authorized for no userRole row at all is refused
  identically whatever the target holds and whether or not the named role exists, so the status
  code carries no membership signal. Measured through the request handler, which is where the
  status code is produced.
"""

import importlib.util
import json
import os

import pytest
from unittest.mock import MagicMock, patch
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.handlers.authz import CasbinEnforcerService
import backend.backend.handlers.authz as authz
from backend.backend.handlers.userRoles import userRolesService


def _real_constants():
    """The genuine common/constants.py, loaded by path under a private module name.

    Same reason as test_userRolesService_operation_authz.py: outside the authz test directory
    the repo-wide conftest installs a bare MagicMock for `common`, with which the real generator
    emits no rules and every verdict is a vacuous False.
    """
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "constants.py"
    )
    spec = importlib.util.spec_from_file_location("_real_common_constants_post", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSTANTS = _real_constants()
PERMISSION_CONSTRAINT_FIELDS = CONSTANTS.PERMISSION_CONSTRAINT_FIELDS
PERMISSION_CONSTRAINT_POLICY = CONSTANTS.PERMISSION_CONSTRAINT_POLICY

_CALLER = "scoped-admin"
_CALLER_ROLE = "assignment-manager"
_TARGET = "target-user"
_SCOPED_ROLE = "viewer"
_OTHER_ROLE = "editor"


@pytest.fixture
def real_constraint_constants():
    """Point the authz module at the real constants for the duration of a test."""
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


def _role_scoped_policy():
    """A constraint scoped `roleName equals 'viewer'` with POST allow."""
    return {
        "constraintId": "c1",
        "objectType": "userRole",
        "criteriaAnd": [{"field": "roleName", "operator": "equals", "value": _SCOPED_ROLE}],
        "criteriaOr": [],
        "groupPermissions": [
            {"groupId": _CALLER_ROLE, "permission": "POST", "permissionType": "allow"}
        ],
        "userPermissions": [],
    }


def _real_enforcer(policies):
    """The real policy text generator plus the real Casbin enforcer, DynamoDB reads stubbed."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = _CALLER
    svc._mfaEnabled = True
    user_roles = [{"userId": _CALLER, "roleName": _CALLER_ROLE}]
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


@pytest.mark.unit
class TestARoleScopedConstraintCanGrantItsOwnAssignment:
    """The capability a set-level POST check took away, measured on the real enforcer."""

    def test_the_generator_is_not_vacuous(self, real_constraint_constants):
        """Control: without the constants repair every verdict below is a vacuous False."""
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        rules = svc._generate_criteria_object_rules(
            [{"field": "roleName", "operator": "equals", "value": _SCOPED_ROLE}],
            object_type="userRole",
        )
        assert rules, (
            "the real rule generator produced no rule for a userRole criterion; the constants "
            "repair is not in effect and every verdict here would be vacuous"
        )

    def test_the_scoped_assignment_is_allowed(self, real_constraint_constants):
        svc = _real_enforcer([_role_scoped_policy()])
        assert svc.enforce(
            {"userId": _TARGET, "roleName": _SCOPED_ROLE, "object__type": "userRole"}, "POST"
        ) is True, (
            "a constraint scoped to one role name cannot grant that role, so a role-scoped "
            "assignment manager can do nothing at all"
        )

    def test_another_role_is_still_refused(self, real_constraint_constants):
        """Control: the grant above is scoped, not a blanket allow."""
        svc = _real_enforcer([_role_scoped_policy()])
        assert svc.enforce(
            {"userId": _TARGET, "roleName": _OTHER_ROLE, "object__type": "userRole"}, "POST"
        ) is False

    def test_the_set_level_object_is_still_refused(self, real_constraint_constants):
        """Why POST must not be checked at set level: this is the verdict it would get.

        The whole-set operations keep this check on purpose -- PUT and DELETE can remove rows the
        request never names, so authority over the set as a whole is the right subject. The same
        check on POST, which can only add the rows it names, is what refuses the grant above.
        """
        svc = _real_enforcer([_role_scoped_policy()])
        assert svc.enforce({"userId": _TARGET, "object__type": "userRole"}, "POST") is False
        assert PERMISSION_CONSTRAINT_FIELDS["roleName"] == "", (
            "the missing roleName is no longer filled with the empty string; the refusal above "
            "may no longer be about the placeholder"
        )


def _event(method, body):
    return {
        "requestContext": {"http": {"method": method, "path": "/userRoles"}},
        "pathParameters": None,
        "queryStringParameters": None,
        "body": json.dumps(body),
        "headers": {"authorization": "Bearer test-token"},
    }


def _typed_assignments(role_names):
    return [
        {
            "userId": {"S": _TARGET},
            "roleName": {"S": role},
            "createdOn": {"S": "2026-01-01T00:00:00"},
        }
        for role in role_names
    ]


class _DenyAllEnforcer:
    """A caller authorized for no userRole row at all."""

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, action):
        return False

    def enforceAPI(self, event):
        return True


def _post_status(existing, known_roles):
    """POST one assignment through the request handler with everything else stubbed."""
    user_roles_table = MagicMock()
    batch = MagicMock()
    user_roles_table.batch_writer.return_value.__enter__.return_value = batch

    def _get_role(role):
        return [{"roleName": {"S": role}}] if role in known_roles else []

    saved_claims = userRolesService.claims_and_roles
    patches = [
        patch.object(userRolesService, "CasbinEnforcer", _DenyAllEnforcer),
        patch.object(userRolesService, "user_roles_table", user_roles_table),
        patch.object(userRolesService, "log_auth_changes", MagicMock()),
        patch.object(
            userRolesService,
            "get_all_roles_for_user",
            MagicMock(return_value=_typed_assignments(existing)),
        ),
        patch.object(userRolesService, "get_role", MagicMock(side_effect=_get_role)),
    ]
    for p in patches:
        p.start()
    userRolesService.claims_and_roles = {"tokens": [_CALLER]}
    try:
        response = userRolesService.handle_post_request(
            _event("POST", {"userId": _TARGET, "roleName": [_SCOPED_ROLE]})
        )
    finally:
        userRolesService.claims_and_roles = saved_claims
        for p in reversed(patches):
            p.stop()
    return response, batch


@pytest.mark.unit
class TestTheMinItemsPremiseIsPinned:
    """The model constraint the removal of the set-level POST check rests on.

    `create_user_roles` authorizes exactly the rows the request names, in a loop over `roleName`,
    and that loop is the WHOLE authorization for POST. It is only unconditional while the list
    cannot be empty: with an empty list the loop runs zero times, nothing is evaluated at all, and
    the create falls through to the duplicate and existence checks with no verdict behind it --
    which is precisely the state the set-level check was there to prevent.
    `CreateUserRolesRequestModel.roleName` carries `min_items=1`, and the rest of this file (and
    `authorize_user_role_set_operation`'s docstring) reasons from that premise, so it is asserted
    here rather than only stated.

    Pydantic v1 folds an unrecognised `Field()` keyword into `field_info.extra` instead of raising,
    so a v2 spelling would leave the field unconstrained while every test still passed. The
    assertion is therefore on the PARSED field, not on the declaration text.
    """

    def test_role_name_requires_at_least_one_entry_on_the_parsed_field(self):
        field_info = userRolesService.CreateUserRolesRequestModel.__fields__["roleName"].field_info

        assert field_info.min_items == 1, (
            f"CreateUserRolesRequestModel.roleName no longer requires at least one role "
            f"(min_items={field_info.min_items!r}). create_user_roles authorizes only the rows "
            f"the request names, so an empty list makes POST evaluate nothing at all -- the "
            f"set-level check that would have covered it was removed on this premise"
        )
        assert not field_info.extra, (
            f"a Field() keyword was swallowed into field_info.extra and validates nothing: "
            f"{field_info.extra}"
        )

    def test_an_empty_role_list_never_reaches_the_authorization_loop(self):
        """The premise as behaviour: the request is refused before create_user_roles is entered."""
        create = MagicMock()
        with patch.object(userRolesService, "create_user_roles", create), \
                patch.object(userRolesService, "log_auth_changes", MagicMock()):
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _TARGET, "roleName": []})
            )

        assert response["statusCode"] == 400, response
        create.assert_not_called()

    def test_a_single_role_list_is_still_accepted(self):
        """Positive control: the bound is at zero, not at one -- one role must still parse."""
        parsed = userRolesService.CreateUserRolesRequestModel(
            userId=_TARGET, roleName=[_SCOPED_ROLE]
        )
        assert parsed.roleName == [_SCOPED_ROLE]


@pytest.mark.unit
class TestRemovingTheSetLevelCheckLeavesTheOracleClosed:
    """The per-named-row loop alone refuses, in every state of the target.

    The oracle the set-level check was added for: a caller authorized for no userRole row gets a
    verdict that depends on the target's membership, so only the correct guess answers 200. POST
    cannot carry it, because `roleName` has `min_items=1` -- the loop always runs at least once,
    before the duplicate check and before the role-existence check.
    """

    @pytest.mark.parametrize(
        "label,existing,known_roles",
        [
            ("target holds the named role", (_SCOPED_ROLE,), (_SCOPED_ROLE,)),
            ("target holds another role", (_OTHER_ROLE,), (_SCOPED_ROLE, _OTHER_ROLE)),
            ("target holds nothing", (), (_SCOPED_ROLE,)),
            ("named role does not exist", (), ()),
            ("named role does not exist and target holds it", (_SCOPED_ROLE,), ()),
        ],
    )
    def test_a_caller_authorized_for_nothing_gets_403(self, label, existing, known_roles):
        response, batch = _post_status(existing, known_roles)

        assert response["statusCode"] == 403, (
            f"{label}: a caller authorized for no userRole row was given an answer that "
            f"depends on the target's membership or on role existence: {response}"
        )
        assert json.loads(response["body"])["message"] == "Not Authorized"
        batch.put_item.assert_not_called()

    def test_the_status_code_is_identical_across_every_state(self):
        """The oracle stated directly: one status code for all five states."""
        statuses = {}
        for label, existing, known_roles in (
            ("holds it", (_SCOPED_ROLE,), (_SCOPED_ROLE,)),
            ("holds another", (_OTHER_ROLE,), (_SCOPED_ROLE, _OTHER_ROLE)),
            ("holds nothing", (), (_SCOPED_ROLE,)),
            ("role deleted", (), ()),
        ):
            statuses[label] = _post_status(existing, known_roles)[0]["statusCode"]

        assert set(statuses.values()) == {403}, (
            f"the POST status code distinguishes the target's role set from a wrong guess: "
            f"{statuses}"
        )

    def test_an_authorized_caller_is_still_served(self):
        """Positive control: the deny-all above is the stand-in, not the handler.

        Without this every assertion in the class is satisfied by a handler that refuses
        everything, including the roleName-scoped grant this change exists to restore.
        """
        user_roles_table = MagicMock()
        batch = MagicMock()
        user_roles_table.batch_writer.return_value.__enter__.return_value = batch

        class _AllowScopedRole:
            def __init__(self, claims_and_roles):
                pass

            def enforce(self, obj, action):
                return obj.get("roleName") == _SCOPED_ROLE

            def enforceAPI(self, event):
                return True

        saved_claims = userRolesService.claims_and_roles
        patches = [
            patch.object(userRolesService, "CasbinEnforcer", _AllowScopedRole),
            patch.object(userRolesService, "user_roles_table", user_roles_table),
            patch.object(userRolesService, "log_auth_changes", MagicMock()),
            patch.object(
                userRolesService, "get_all_roles_for_user", MagicMock(return_value=[])
            ),
            patch.object(
                userRolesService,
                "get_role",
                MagicMock(return_value=[{"roleName": {"S": _SCOPED_ROLE}}]),
            ),
        ]
        for p in patches:
            p.start()
        userRolesService.claims_and_roles = {"tokens": [_CALLER]}
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _TARGET, "roleName": [_SCOPED_ROLE]})
            )
        finally:
            userRolesService.claims_and_roles = saved_claims
            for p in reversed(patches):
                p.stop()

        assert response["statusCode"] == 200, (
            f"a caller scoped to exactly the role they are granting was refused: {response}"
        )
        assert batch.put_item.call_args.kwargs["Item"]["roleName"] == _SCOPED_ROLE
