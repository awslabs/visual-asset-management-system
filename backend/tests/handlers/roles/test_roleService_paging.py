# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The user-role cascade in roleService ends on the PRESENCE of LastEvaluatedKey.

DELETE /roles/{roleId} removes every ``(userId, roleName)`` assignment before removing the role row.
A single ``scan`` page covers at most 1 MB of SCANNED data and the ``roleName`` filter is applied
after that cap, so completeness requires paging to exhaustion (backend/CLAUDE.md Rule 14): an
assignment left behind re-attaches silently to any role later created under the same name.

BOUNDED? The page walk is unbounded on purpose -- exhaustion is the property. The accumulated
``assignment_keys`` list therefore grows with the number of assignments, but it is never returned to
a caller, so Rule 15 (response bounds) does not apply to it; a caller-visible ceiling here would mean
a partial cascade, which is the defect this cascade exists to avoid. The unbounded in-memory
accumulation on the delete path is recorded as an open item rather than changed here.

The loop's FORM is what these tests pin. ``scan_response.get('LastEvaluatedKey')`` answers with a
truthy child mock forever against an under-stubbed reader, so the value form does not fail, it HANGS
-- and a timeout raises no assertion, so it names no test. Sibling coverage for the cascade's
completeness, its Casbin gate and its abort-on-failure contract lives in
``test_roleService_delete_cascade.py``; this file covers only the cursor threading and the
termination form.
"""

from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import ConditionBase, ConditionExpressionBuilder

from backend.backend.handlers.roles import roleService
from backend.tests.pagingStub import BareMockReader, Pager

_ROLE = "paging-role"


def _read_filter(call):
    """A read's scoping expression and the values it binds, as two texts.

    Lets a test assert the read's SCOPE without pinning one spelling of it. A boto3 condition
    object is rendered through the builder boto3 itself uses, so the string form
    (``'roleName = :roleName'`` plus ``ExpressionAttributeValues``) and the equivalent
    ``Attr('roleName').eq(role)`` form both show the attribute name and the bound role name.

    **Both `KeyConditionExpression` and `FilterExpression` are read.** A cascade that scopes itself
    with a GSI query on ``roleName`` carries no ``FilterExpression`` at all -- and that is the
    strictly BETTER implementation, since the key condition is applied before the 1 MB page read
    rather than after it. A helper that inspected only the filter would fail the improvement it
    should welcome, while the regression it exists to catch (a read that stops naming the role) is
    identical in either form.

    The two returned texts are kept APART on purpose: rendered together, a read whose scoping
    expression was dropped still shows ``roleName`` in its leftover ``:roleName`` placeholder value,
    and the assertion would pass over a read that scopes nothing.
    """
    expressions = []
    values = []
    for field in ("KeyConditionExpression", "FilterExpression"):
        expression = call.get(field)
        if expression is None:
            continue
        if isinstance(expression, ConditionBase):
            built = ConditionExpressionBuilder().build_expression(
                expression, is_key_condition=(field == "KeyConditionExpression"))
            expressions.append(
                f"{built.condition_expression} {built.attribute_name_placeholders}")
            values.append(f"{built.attribute_value_placeholders}")
        else:
            expressions.append(f"{expression}")

    # Only the caller-supplied values map contributes to the bound-values text for a string-form
    # expression, so a dropped expression cannot be rescued by a leftover placeholder.
    if call.get("ExpressionAttributeValues") is not None:
        values.append(f"{call.get('ExpressionAttributeValues')}")

    return " ".join(expressions) or "None", " ".join(values) or "None"


def _delete_event():
    return {
        "requestContext": {"http": {"method": "DELETE", "path": f"/roles/{_ROLE}"}},
        "pathParameters": {"roleId": _ROLE},
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
    }


class _AllowAll:
    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, obj, action):
        return True

    def enforceAPI(self, event):
        return True


def _wire(reader):
    """Patch the module's tables and enforcer; returns (roles_table, user_roles_table, batch, undo)."""
    user_roles_table = MagicMock()
    user_roles_table.scan.side_effect = reader

    batch = MagicMock()
    user_roles_table.batch_writer.return_value.__enter__.return_value = batch

    roles_table = MagicMock()

    saved_claims = roleService.claims_and_roles
    patches = [
        patch.object(roleService, "CasbinEnforcer", _AllowAll),
        patch.object(roleService, "user_roles_table", user_roles_table),
        patch.object(roleService, "roles_table", roles_table),
        patch.object(roleService, "log_auth_changes", MagicMock()),
    ]
    for started in patches:
        started.start()
    roleService.claims_and_roles = {"tokens": ["tester"]}

    def _undo():
        roleService.claims_and_roles = saved_claims
        for started in reversed(patches):
            started.stop()

    return roles_table, user_roles_table, batch, _undo


def _assignment_pages():
    return (
        {"Items": [{"userId": "u1", "roleName": _ROLE}],
         "LastEvaluatedKey": {"userId": "u1", "roleName": _ROLE}},
        {"Items": [{"userId": "u2", "roleName": _ROLE}],
         "LastEvaluatedKey": {"userId": "u2", "roleName": _ROLE}},
        {"Items": [{"userId": "u3", "roleName": _ROLE}]},
    )


@pytest.mark.unit
class TestCascadeCursorThreading:
    def test_every_page_cursor_is_resumed_from(self):
        pager = Pager(*_assignment_pages(), name="roleService delete cascade")
        roles_table, user_roles_table, batch, undo = _wire(pager)
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 200, response
        # Every cursor the stub handed out was resumed from, which is what proves the final page was
        # reached. Stated over the SET of cursors, so no read count or read order is pinned.
        pager.assert_paged_to_exhaustion()
        # Pages are served BY CURSOR here, so the assignments below can only all be present if each
        # continuation asked for the right page.
        deleted = {
            frozenset(call.kwargs["Key"].items())
            for call in batch.delete_item.call_args_list
        }
        assert deleted == {
            frozenset({"userId": "u1", "roleName": _ROLE}.items()),
            frozenset({"userId": "u2", "roleName": _ROLE}.items()),
            frozenset({"userId": "u3", "roleName": _ROLE}.items()),
        }, f"assignments deleted: {[dict(key) for key in deleted]}"

    def test_the_filter_survives_every_continuation(self):
        """A continuation that lost the filter would collect other roles' assignments."""
        pager = Pager(*_assignment_pages(), name="roleService delete cascade")
        roles_table, user_roles_table, batch, undo = _wire(pager)
        try:
            roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        # An "every read ..." assertion holds trivially over ZERO reads, and over a single read it
        # says nothing about a CONTINUATION. Both are established first: the pages are served by
        # cursor, so reaching the final page requires the continuations to have happened.
        pager.assert_paged_to_exhaustion()
        assert pager.resumed_from, (
            "no continuation was issued, so 'every continuation' would assert nothing")
        # Containment rather than the exact expression: a filter WIDENED with a further condition,
        # or one whose placeholder is renamed, is not a regression -- one that stops naming this
        # role is, because the continuation would then collect other roles' assignments.
        for call in pager.calls:
            expression, bound_values = _read_filter(call)
            assert "roleName" in expression, (
                f"a read carries no filter on roleName, so it collects every role's "
                f"assignments: {call}")
            assert _ROLE in bound_values, (
                f"a read does not bind {_ROLE}, so it is not scoped to this role: {call}")


@pytest.mark.unit
class TestCascadeTerminationForm:
    def test_terminates_against_an_under_stubbed_reader(self):
        """The regression guard for the loop FORM, not for the cascade's completeness.

        The reader raises a ``BaseException`` after a capped number of reads, so the value form fails
        with a message instead of hanging -- and neither the cascade's ``except Exception`` nor the
        handler's catch-all can swallow it into a plausible-looking error response.
        """
        reader = BareMockReader(name="roleService delete cascade")
        roles_table, user_roles_table, batch, undo = _wire(reader)
        try:
            response = roleService.handle_delete_request(_delete_event())
        finally:
            undo()

        assert response["statusCode"] == 200, response
        # It read at least once and then stopped on the absent key.
        assert reader.calls, "the cascade never read the assignments table"
        # The walk finished, so the role row was removed rather than the request being abandoned.
        # Stated over the SET of removals, and over the guard's PRESENCE rather than its exact
        # text: a retried delete, or a condition written as a boto3 condition object, is not a
        # regression -- removing a different row, or removing it unguarded, is.
        removals = {
            (call.kwargs.get("Key", {}).get("roleName"),
             bool(call.kwargs.get("ConditionExpression")))
            for call in roles_table.delete_item.call_args_list
        }
        assert (_ROLE, True) in removals, f"role-row removals: {removals}"
