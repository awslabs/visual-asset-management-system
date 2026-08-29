# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""DELETE /roles/{roleId}: fail-closed Tier-2 authorization and a complete cascade.

Two properties are pinned here.

**Authorization denies on an empty token list.** `handle_delete_request` evaluates a role
object against Casbin. With no authenticated identity there is nothing to evaluate, so the
request must be refused *before* the enforcer is consulted — the shape backend/CLAUDE.md
Rule 4 prescribes for a single-resource check. The assertion is therefore not only "403":
it is that `CasbinEnforcer` was never constructed. That matters because the enforcer the
suite injects for the other tests is a stand-in whose verdict the test chooses; a handler
that consulted a *real* enforcer with no tokens would produce whatever that enforcer's
default happened to be. Tier 1 in `lambda_handler` also refuses an empty token list, which
is why these tests drive the request handler directly: the property under test is that this
check is fail-closed on its own, not that something upstream happens to cover it.

**The user-role cascade completes or the role survives.** Assignments are keyed
`(userId, roleName)` and are read back by `userId` alone with no join against the roles
table, so an assignment that outlives its role silently re-attaches to any role later
created under the same name. A single `scan` page covers at most 1 MB of *scanned* data and
the `roleName` filter is applied after that cap, so a one-page read drops assignments on any
table of consequence. The cascade therefore pages to exhaustion, and a cascade that cannot
complete aborts the delete rather than reporting success — leaving the role in place, which
is recoverable, instead of orphaned grants, which are not visible at all.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.roles import roleService
# The read-scope renderer lives with the sibling paging tests. Imported rather than re-spelled: it
# is what lets a filter written as a boto3 condition object read the same as the string form, and a
# second copy of that logic would drift from the form the cascade actually uses.
from backend.tests.handlers.roles.test_roleService_paging import _read_filter


_ROLE = "test-role"


class _EnforcerSpy:
    """A CasbinEnforcer stand-in that records every construction and every enforce call.

    Denials are per action rather than a single verdict for everything. A stand-in that refuses
    every action cannot tell "the site under test denied" from "something earlier denied": a
    handler made strictly safer by authorizing an additional action first would short-circuit
    there, and the check actually being measured would never run.
    """

    def __init__(self, denied_actions=()):
        self.denied_actions = set(denied_actions)
        self.constructions = []
        self.calls = []

    @property
    def factory(self):
        spy = self

        class _Enforcer:
            def __init__(self, claims_and_roles):
                spy.constructions.append(claims_and_roles)

            def enforce(self, obj, action):
                spy.calls.append({"object": dict(obj), "action": action})
                return action not in spy.denied_actions

            def enforceAPI(self, event):
                return True

        return _Enforcer


def _delete_event():
    return {
        "requestContext": {"http": {"method": "DELETE", "path": f"/roles/{_ROLE}"}},
        "pathParameters": {"roleId": _ROLE},
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
    }


def _wire(scan_pages, tokens=("tester",), denied_actions=(), batch_error=None):
    """Patch the module's tables and enforcer; returns (spy, roles_table, user_roles_table, batch)."""
    spy = _EnforcerSpy(denied_actions=denied_actions)

    user_roles_table = MagicMock()
    if isinstance(scan_pages, Exception):
        user_roles_table.scan.side_effect = scan_pages
    else:
        user_roles_table.scan.side_effect = list(scan_pages)

    batch = MagicMock()
    if batch_error is not None:
        batch.delete_item.side_effect = batch_error
    user_roles_table.batch_writer.return_value.__enter__.return_value = batch

    roles_table = MagicMock()

    saved_claims = roleService.claims_and_roles
    patches = [
        patch.object(roleService, "CasbinEnforcer", spy.factory),
        patch.object(roleService, "user_roles_table", user_roles_table),
        patch.object(roleService, "roles_table", roles_table),
        patch.object(roleService, "log_auth_changes", MagicMock()),
    ]
    for p in patches:
        p.start()
    roleService.claims_and_roles = {"tokens": list(tokens)}

    def _undo():
        roleService.claims_and_roles = saved_claims
        for p in reversed(patches):
            p.stop()

    return spy, roles_table, user_roles_table, batch, _undo


@pytest.mark.unit
class TestEmptyTokenListDenies:
    """Rule 4, single resource: no identity means deny before the enforcer is consulted."""

    def test_empty_tokens_return_403_without_consulting_casbin(self):
        spy, roles_table, user_roles_table, batch, undo = _wire(
            [{"Items": []}], tokens=()
        )
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"roleService.handle_delete_request returned {response['statusCode']} for a "
            f"request with no authenticated identity: {response}"
        )
        assert spy.constructions == [], (
            f"CasbinEnforcer was constructed for an empty token list "
            f"({len(spy.constructions)} times); with no identity the request must be "
            f"refused before authorization is evaluated"
        )
        assert spy.calls == [], f"enforce() was called with no identity: {spy.calls}"

    def test_empty_tokens_delete_nothing(self):
        """The deny must precede the cascade and the role row delete."""
        spy, roles_table, user_roles_table, batch, undo = _wire(
            [{"Items": [{"userId": "u1"}]}], tokens=()
        )
        try:
            roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        user_roles_table.scan.assert_not_called()
        roles_table.delete_item.assert_not_called()
        batch.delete_item.assert_not_called()


@pytest.mark.unit
class TestTier2VerdictIsHonoured:
    """The positive control for the deny above, plus the object handed to Casbin."""

    def test_a_denied_caller_gets_403_and_the_role_survives(self):
        spy, roles_table, user_roles_table, batch, undo = _wire(
            [{"Items": []}], denied_actions=("DELETE",)
        )
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 403
        roles_table.delete_item.assert_not_called()
        user_roles_table.scan.assert_not_called()

    def test_a_permitted_caller_deletes_the_role(self):
        """Positive control: "denied" is also satisfied by a handler that denies everything."""
        spy, roles_table, user_roles_table, batch, undo = _wire(
            [{"Items": [{"userId": "u1"}]}]
        )
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized role delete was refused: {response}"
        )
        # The row removed, and removed under a guard -- stated over the SET of removals and over the
        # guard's PRESENCE. A retried (idempotent) delete and a condition written as a boto3
        # condition object are both strictly safer than what runs today and must stay green;
        # removing a different row, or removing it unguarded, is the regression.
        removals = {
            (call.kwargs.get("Key", {}).get("roleName"),
             bool(call.kwargs.get("ConditionExpression")))
            for call in roles_table.delete_item.call_args_list
        }
        assert (_ROLE, True) in removals, f"role-row removals: {removals}"

    def test_the_object_handed_to_casbin_is_the_role_being_deleted(self):
        """Without these fields the ABAC rule cannot match and the verdict means nothing.

        Asserted as membership of the evaluated (action, object__type, roleName) set rather
        than as a call count: a handler that authorizes more than the minimum is strictly safer
        and must stay green, while a handler that drops the DELETE check goes red because the
        triple disappears.
        """
        spy, roles_table, user_roles_table, batch, undo = _wire([{"Items": []}])
        try:
            roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        evaluated = {
            (call["action"], call["object"].get("object__type"), call["object"].get("roleName"))
            for call in spy.calls
        }
        assert ("DELETE", "role", _ROLE) in evaluated, (
            f"the role being deleted was never evaluated for DELETE; evaluated: "
            f"{sorted(evaluated)}"
        )


@pytest.mark.unit
class TestCascadePagesToExhaustion:
    """Every assignment is removed, not just the first scan page."""

    def test_assignments_beyond_the_first_page_are_deleted(self):
        pages = [
            {"Items": [{"userId": "u1"}], "LastEvaluatedKey": {"userId": "u1", "roleName": _ROLE}},
            {"Items": [{"userId": "u2"}], "LastEvaluatedKey": {"userId": "u2", "roleName": _ROLE}},
            {"Items": [{"userId": "u3"}]},
        ]
        spy, roles_table, user_roles_table, batch, undo = _wire(pages)
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 200, response
        # The count pin is deliberate here and only here: reading every page IS the property
        # under test, and a stub that hands out three pages cannot be exhausted in fewer than
        # three calls. Stated as a floor so an implementation that retries a throttled page --
        # strictly safer -- stays green.
        assert user_roles_table.scan.call_count >= 3, (
            f"the cascade issued {user_roles_table.scan.call_count} scan call(s) for a "
            f"3-page result; assignments past the first page survive a single-page read"
        )
        # The paging is real: the later pages resume from the keys the earlier ones returned.
        resume_keys = [
            call.kwargs.get("ExclusiveStartKey")
            for call in user_roles_table.scan.call_args_list
        ]
        for expected_key in (
            {"userId": "u1", "roleName": _ROLE},
            {"userId": "u2", "roleName": _ROLE},
        ):
            assert expected_key in resume_keys, (
                f"the cascade never resumed from {expected_key}, so its later reads are not "
                f"continuations of the earlier ones: {resume_keys}"
            )

        # Every assignment removed and no other: order and repetition are immaterial, but an
        # extra key would be a cascade widened into another role's assignments.
        deleted = {
            frozenset(call.kwargs["Key"].items())
            for call in batch.delete_item.call_args_list
        }
        assert deleted == {
            frozenset({"userId": "u1", "roleName": _ROLE}.items()),
            frozenset({"userId": "u2", "roleName": _ROLE}.items()),
            frozenset({"userId": "u3", "roleName": _ROLE}.items()),
        }, f"assignments deleted: {[dict(key) for key in deleted]}"

    def test_the_filter_still_targets_only_this_role(self):
        """Control: paging must not widen the cascade into other roles' assignments."""
        spy, roles_table, user_roles_table, batch, undo = _wire(
            [{"Items": [{"userId": "u1"}]}]
        )
        try:
            roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        # Both readers, not just `scan`: a cascade that scopes itself with a GSI query on roleName
        # carries no scan call at all, and that is the better implementation -- the key condition is
        # applied before the 1 MB page read rather than after it. Collecting only scans would make
        # this control fail the improvement instead of the regression.
        reads = [call.kwargs for call in user_roles_table.scan.call_args_list]
        reads += [call.kwargs for call in user_roles_table.query.call_args_list]
        assert reads, "the cascade never read the assignments table"
        # Containment rather than the exact expression: a filter WIDENED with a further condition,
        # a renamed placeholder, or one written as a boto3 condition object still scopes the read to
        # this role. A read that stops naming the role is the regression, because the cascade would
        # then collect other roles' assignments.
        for kwargs in reads:
            expression, bound_values = _read_filter(kwargs)
            assert "roleName" in expression, (
                f"a read carries no filter on roleName, so the cascade collects every role's "
                f"assignments: {kwargs}")
            assert _ROLE in bound_values, (
                f"a read does not bind {_ROLE}, so it is not scoped to this role: {kwargs}")


@pytest.mark.unit
class TestCascadeFailureAbortsTheDelete:
    """A cascade that cannot complete must not report success."""

    @pytest.mark.parametrize(
        "site,kwargs",
        [
            ("scan", {"scan_pages": RuntimeError("scan unavailable")}),
            (
                "batch delete",
                {
                    "scan_pages": [{"Items": [{"userId": "u1"}]}],
                    "batch_error": RuntimeError("delete throttled"),
                },
            ),
        ],
    )
    def test_a_failed_cascade_leaves_the_role_in_place(self, site, kwargs):
        spy, roles_table, user_roles_table, batch, undo = _wire(**kwargs)
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] != 200, (
            f"a failed cascade ({site}) still reported success: {response}"
        )
        roles_table.delete_item.assert_not_called()

    def test_the_failure_message_names_no_request_input(self):
        """Rule 11: the caller learns the delete did not happen, not what was submitted."""
        spy, roles_table, user_roles_table, batch, undo = _wire(
            RuntimeError("scan unavailable")
        )
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        message = json.loads(response["body"])["message"]
        assert _ROLE not in message, f"the error message echoes the role name: {message}"
        assert "scan unavailable" not in message, (
            f"the error message leaks the underlying failure: {message}"
        )
        assert "not deleted" in message.lower(), (
            f"the message does not tell the caller the role survived: {message}"
        )


@pytest.mark.unit
class TestTier1StillDeniesEmptyTokens:
    """The upstream control that masks the Tier-2 defect in production stays in place."""

    def test_lambda_handler_denies_an_empty_token_list(self):
        spy = _EnforcerSpy()
        with patch.object(roleService, "CasbinEnforcer", spy.factory), patch.object(
            roleService, "request_to_claims", MagicMock(return_value={"tokens": []})
        ):
            response = roleService.lambda_handler(_delete_event(), MagicMock())

        assert response["statusCode"] == 403
        assert spy.constructions == [], (
            f"Tier 1 constructed an enforcer for an empty token list: {spy.constructions}"
        )
