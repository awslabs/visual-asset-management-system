# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""PUT /roles is a partial update: a field the request omits keeps its stored value.

`UpdateRoleRequestModel` declares `source`, `sourceIdentifier` and `mfaRequired` as optional,
and `handle_put_request` passes `dict(exclude_unset=True)` — so a body that names only
`roleName` and `description` reaches `update_role` with those two keys and nothing else. The
update expression is therefore built from the keys actually present.

The field that makes this load-bearing is `mfaRequired`. An expression that always assigns it
rewrites an MFA-gated role to `mfaRequired=False` on a description-only edit, and
`CasbinEnforcerService._read_mfaNotRequired_roles_from_table` then activates that role in
non-MFA sessions — an authentication control removed by an edit that never mentioned it. The
same expression nulls `source`/`sourceIdentifier`, severing a role's IDP linkage.

`vamscli role update` is the shipped client that hits this: it adds `mfaRequired` to the
payload only when `--mfa-required`/`--no-mfa-required` is passed, and documents
`--description` on its own as a partial edit.

Every "left alone" assertion is paired with an "explicitly supplied" one for the same field,
because a handler that had simply stopped writing `mfaRequired` altogether would satisfy the
first set on its own while making the MFA gate unsettable.

The stored item after the call is what these assert, not the expression string: asserting on
the string alone cannot tell "the field was left alone" from "the field was reassigned the
value it already held", and only the former survives a concurrent writer.
"""

import json
from contextlib import contextmanager

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

from backend.backend.handlers.roles import createRole


_ROLE = "test-role"

# The role as stored: MFA-gated and linked to an external IDP group.
_STORED_MFA_ROLE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "roleName": _ROLE,
    "description": "original text",
    "createdOn": "2026-01-01T00:00:00",
    "source": "INTERNAL_SYSTEM",
    "sourceIdentifier": "idp-group-42",
    "mfaRequired": True,
}


def _real_to_update_expr(record, op="SET"):
    """The real `common.dynamodb.to_update_expr`.

    The handler binds `to_update_expr` at import time, and `tests/conftest.py` registers
    `sys.modules['common.dynamodb']` as a `MagicMock`, so the bound name is a mock whose call
    yields nothing to unpack into three values. Patching the real logic in is what makes the
    expression the handler builds observable — the same approach as
    `tests/handlers/auth/test_apiKeyService_user_scope.py`.
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names)
    )
    return keys_map, values_map, expr


class _FakeRolesTable:
    """A roles table that APPLIES the update expression to the stored item.

    Only `SET` is supported, which is all `update_role` emits. A `SET` on the partition key
    is rejected the way DynamoDB rejects it, so a builder that leaves `roleName` in the
    update dict fails here rather than passing on an expression that would 400 in production.
    """

    def __init__(self, item=None):
        self.item = dict(item) if item is not None else None
        self.update_calls = []
        self.assigned_per_call = []

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)

        if self.item is None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no role"}},
                "UpdateItem",
            )

        names = kwargs.get("ExpressionAttributeNames") or {}
        values = kwargs["ExpressionAttributeValues"]
        expression = kwargs["UpdateExpression"]

        assert expression.startswith("SET "), (
            f"only SET is emitted by update_role; got {expression!r}"
        )
        assigned = set()
        for clause in expression[len("SET "):].split(","):
            name_ref, value_ref = (part.strip() for part in clause.split("="))
            # A name placeholder resolves through the map; a bare attribute name is
            # legal in an expression and stands for itself.
            attribute = names.get(name_ref, name_ref)
            assert attribute != "roleName", (
                "roleName is the table's partition key; DynamoDB rejects a SET on a key "
                "attribute with a ValidationException, so every update would 400"
            )
            self.item[attribute] = values[value_ref]
            assigned.add(attribute)
        self.assigned_per_call.append(assigned)
        return {}

    def put_item(self, **kwargs):
        raise AssertionError("put_item must not be called on the update path")

    @property
    def update_kwargs(self):
        assert len(self.update_calls) == 1, (
            f"expected exactly one update_item call, saw {len(self.update_calls)}"
        )
        return self.update_calls[0]

    @property
    def updated_attributes(self):
        """The attribute names the expression actually assigned.

        Read off the parsed clauses rather than off ExpressionAttributeNames, whose entries
        need not all appear in the expression -- a name declared and never referenced would
        otherwise be reported as written.
        """
        assert len(self.assigned_per_call) == 1, (
            f"expected exactly one applied update, saw {len(self.assigned_per_call)}"
        )
        return set(self.assigned_per_call[0])


def _enforcer_factory(denied_actions=()):
    denied = set(denied_actions)

    class _Enforcer:
        def __init__(self, claims_and_roles):
            pass

        def enforce(self, obj, action):
            return action not in denied

        def enforceAPI(self, event):
            return True

    return _Enforcer


@contextmanager
def _wired(stored=_STORED_MFA_ROLE, denied_actions=()):
    table = _FakeRolesTable(stored)
    audit = MagicMock()
    saved_claims = createRole.claims_and_roles
    createRole.claims_and_roles = {"tokens": ["tester"]}
    try:
        with patch.object(createRole, "CasbinEnforcer", _enforcer_factory(denied_actions)), \
                patch.object(createRole, "roles_table", table), \
                patch.object(createRole, "log_auth_changes", audit), \
                patch.object(createRole, "to_update_expr", _real_to_update_expr):
            yield table, audit
    finally:
        createRole.claims_and_roles = saved_claims


def _put_event(body):
    return {
        "requestContext": {"http": {"method": "PUT", "path": "/roles"}},
        "pathParameters": None,
        "queryStringParameters": None,
        "body": json.dumps(body),
        "headers": {"authorization": "Bearer test-token"},
    }


# `source` is deliberately absent from every request BODY below: the model's validator imports
# ALLOWED_ROLE_SOURCES from common.constants, which this harness stubs without it. The
# source/sourceIdentifier matrix is covered by calling update_role directly, which is where
# the expression is built anyway.


@pytest.mark.unit
class TestOmittedFieldsKeepTheirStoredValue:
    """A description-only PUT must not touch any other attribute."""

    def test_a_description_only_update_leaves_the_mfa_gate_intact(self):
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert response["statusCode"] == 200, f"the update was refused: {response}"
        assert table.item["description"] == "new text"
        assert table.item["mfaRequired"] is True, (
            "a PUT that never mentioned mfaRequired rewrote it to "
            f"{table.item['mfaRequired']!r}; the role's MFA requirement was silently removed "
            "and the role becomes active in non-MFA sessions"
        )

    def test_a_description_only_update_leaves_the_idp_linkage_intact(self):
        with _wired() as (table, _audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert table.item["source"] == "INTERNAL_SYSTEM", (
            f"source was rewritten to {table.item['source']!r} by a description-only update"
        )
        assert table.item["sourceIdentifier"] == "idp-group-42", (
            "sourceIdentifier was rewritten to "
            f"{table.item['sourceIdentifier']!r} by a description-only update"
        )

    def test_the_expression_names_only_the_supplied_field(self):
        with _wired() as (table, _audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert table.updated_attributes == {"description"}, (
            "the update expression assigned attributes the request never supplied: "
            f"{sorted(table.updated_attributes)}"
        )

    def test_update_role_called_directly_writes_only_the_keys_it_was_given(self):
        """The business function's own contract, with the model out of the picture."""
        with _wired() as (table, _audit):
            createRole.update_role(
                {"roleName": _ROLE, "sourceIdentifier": "idp-group-99"},
                {"tokens": ["tester"]},
            )

        assert table.updated_attributes == {"sourceIdentifier"}
        assert table.item["sourceIdentifier"] == "idp-group-99"
        assert table.item["description"] == "original text"
        assert table.item["source"] == "INTERNAL_SYSTEM"
        assert table.item["mfaRequired"] is True


@pytest.mark.unit
class TestExplicitlySuppliedFieldsStillWrite:
    """Positive control: partial update must not make a field unsettable."""

    def test_an_explicit_false_still_clears_the_mfa_gate(self):
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event(
                    {"roleName": _ROLE, "description": "new text", "mfaRequired": False}
                )
            )

        assert response["statusCode"] == 200, f"the update was refused: {response}"
        assert table.item["mfaRequired"] is False, (
            "an explicit mfaRequired=false no longer clears the gate; a partial update must "
            "still honour a field the caller did supply"
        )
        assert "mfaRequired" in table.updated_attributes

    def test_an_explicit_true_still_sets_the_mfa_gate(self):
        ungated = dict(_STORED_MFA_ROLE, mfaRequired=False)
        with _wired(stored=ungated) as (table, _audit):
            response = createRole.handle_put_request(
                _put_event(
                    {"roleName": _ROLE, "description": "new text", "mfaRequired": True}
                )
            )

        assert response["statusCode"] == 200, f"the update was refused: {response}"
        assert table.item["mfaRequired"] is True

    def test_a_full_body_update_writes_every_supplied_field(self):
        """The web form's shape — CreateRoles.tsx always sends the whole record."""
        ungated = dict(_STORED_MFA_ROLE, mfaRequired=False)
        with _wired(stored=ungated) as (table, _audit):
            response = createRole.handle_put_request(
                _put_event(
                    {
                        "roleName": _ROLE,
                        "description": "new text",
                        "sourceIdentifier": "idp-group-77",
                        "mfaRequired": True,
                    }
                )
            )

        assert response["statusCode"] == 200, f"the update was refused: {response}"
        assert table.updated_attributes == {
            "description",
            "sourceIdentifier",
            "mfaRequired",
        }
        assert table.item["description"] == "new text"
        assert table.item["sourceIdentifier"] == "idp-group-77"
        assert table.item["mfaRequired"] is True

    def test_an_explicit_null_still_clears_the_source_linkage(self):
        with _wired() as (table, _audit):
            createRole.update_role(
                {"roleName": _ROLE, "source": None, "sourceIdentifier": None},
                {"tokens": ["tester"]},
            )

        assert table.updated_attributes == {"source", "sourceIdentifier"}
        assert table.item["source"] is None
        assert table.item["sourceIdentifier"] is None


@pytest.mark.unit
class TestKeyAndConditionAreUnchanged:
    """The parts of the call that identify and guard the row."""

    def test_the_role_is_addressed_by_key_and_never_assigned(self):
        with _wired() as (table, _audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert table.update_kwargs["Key"] == {"roleName": _ROLE}
        assert "roleName" not in table.updated_attributes

    def test_the_existence_condition_is_still_applied(self):
        with _wired() as (table, _audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        # Containment: the claim is that an existence condition guards the update, not that the
        # expression reads exactly this. A condition widened with a further AND term is strictly
        # safer and would fail an equality, so equality here would push back on an improvement.
        assert "attribute_exists(roleName)" in table.update_kwargs["ConditionExpression"]

    def test_updating_a_role_that_does_not_exist_is_a_400(self):
        """The ConditionalCheckFailedException branch must still be inside the try."""
        with _wired(stored=None) as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert response["statusCode"] == 400, (
            f"a missing role surfaced as {response['statusCode']}: {response}"
        )
        # The handler re-emits VAMSGeneralErrorResponse via general_error(), and that class
        # prepends "VAMS General Error: " to the message, so the body carries the prefix too.
        # The claim under test is that the caller is told the role does not exist rather than
        # being handed a generic failure, so assert the substance and let the prefix be.
        assert "Role does not exist" in json.loads(response["body"])["message"]

    def test_an_update_with_no_assignable_field_is_rejected(self):
        """Nothing but the key: refused before DynamoDB sees an empty SET clause.

        Raised as VAMSGeneralErrorResponse, which the request handler maps to a 400.
        """
        with _wired() as (table, _audit):
            with pytest.raises(createRole.VAMSGeneralErrorResponse):
                createRole.update_role({"roleName": _ROLE}, {"tokens": ["tester"]})

        assert table.update_calls == [], (
            "an update with no assignable attribute reached DynamoDB: "
            f"{table.update_calls}"
        )


@pytest.mark.unit
class TestAuditRecordMatchesWhatWasSubmitted:
    """The audit entry is the only signal an operator has that the MFA gate moved."""

    def test_mfa_required_is_not_recorded_when_the_request_omitted_it(self):
        with _wired() as (_table, audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        details = audit.call_args.args[2]
        assert "mfaRequired" not in details, (
            "the audit entry records mfaRequired for a request that never supplied it "
            f"({details.get('mfaRequired')!r}), so an operator reading the log cannot tell "
            "a real MFA change from the request model's default"
        )
        assert details["roleName"] == _ROLE
        assert details["operation"] == "update"

    def test_mfa_required_is_recorded_when_the_request_supplied_it(self):
        with _wired() as (_table, audit):
            createRole.handle_put_request(
                _put_event(
                    {"roleName": _ROLE, "description": "new text", "mfaRequired": False}
                )
            )

        details = audit.call_args.args[2]
        assert details["mfaRequired"] is False, (
            f"a submitted mfaRequired was not recorded: {details}"
        )


@pytest.mark.unit
class TestDescriptionIsOptionalOnUpdate:
    """`description` is optional too, so a genuine single-field update works.

    Two gates used to require it -- the model's own declaration and a pre-parse check in
    `handle_put_request` -- which made two DOCUMENTED `vamscli role update` invocations 400:
    `role update -r admin --mfa-required` and `role update -r admin --source X
    --source-identifier Y` build a payload with no description. Both are asserted here in the
    exact shape the CLI sends.

    Each acceptance is paired with the thing that must still be refused, because a relaxation
    that stopped checking anything would satisfy the acceptances on its own.
    """

    def test_an_mfa_only_update_is_accepted_and_leaves_the_description_alone(self):
        # The payload `vamscli role update -r <role> --mfa-required` sends.
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "mfaRequired": True})
            )

        assert response["statusCode"] == 200, (
            f"an mfaRequired-only update was refused: {response}"
        )
        assert table.updated_attributes == {"mfaRequired"}
        assert table.item["description"] == "original text", (
            f"a request that never named description rewrote it to {table.item['description']!r}"
        )

    def test_a_source_only_update_is_accepted_and_leaves_the_description_alone(self):
        # The payload `vamscli role update -r <role> --source-identifier <id>` sends. `source`
        # itself is omitted for the ALLOWED_ROLE_SOURCES reason noted above.
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "sourceIdentifier": "idp-group-99"})
            )

        assert response["statusCode"] == 200, (
            f"a sourceIdentifier-only update was refused: {response}"
        )
        assert table.updated_attributes == {"sourceIdentifier"}
        assert table.item["description"] == "original text"

    def test_a_supplied_description_still_writes(self):
        # The paired control: optional must not mean ignored.
        with _wired() as (table, _audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert table.item["description"] == "new text"

    def test_a_body_carrying_nothing_but_the_role_name_is_still_a_400(self):
        """The relaxation removed the description gate, not every gate.

        With description optional, `{roleName}` alone parses -- so `update_role`'s own
        "no assignable field" guard is what refuses it, and it must still be reached.
        """
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(_put_event({"roleName": _ROLE}))

        assert response["statusCode"] == 400, (
            f"an update naming only roleName was accepted: {response}"
        )
        assert table.update_calls == []
        assert table.item["description"] == "original text"

    def test_a_body_with_no_role_name_is_still_a_400(self):
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(_put_event({"description": "new text"}))

        assert response["statusCode"] == 400
        assert "roleName" in json.loads(response["body"])["message"]
        assert table.update_calls == []

    def test_an_empty_description_is_still_refused(self):
        # Optional means omittable, not blankable: min_length=1 still applies to a supplied value.
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": ""})
            )

        assert response["statusCode"] == 400, (
            f"an empty description was accepted: {response}"
        )
        assert table.update_calls == []
        assert table.item["description"] == "original text"

    def test_an_explicit_null_description_is_refused_rather_than_written(self):
        """Unlike source/sourceIdentifier, a null description cannot be stored.

        `RoleResponseModel.description` is a required `str`, so a NULL in the table makes the
        role fail response validation -- degrading `GET /roles` for every caller, not just the
        one who sent it. Omitting the field is how a caller leaves it alone.
        """
        with _wired() as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": None})
            )

        assert response["statusCode"] == 400, (
            f"an explicit null description was accepted: {response}"
        )
        assert table.update_calls == []
        assert table.item["description"] == "original text"

    def test_the_audit_entry_omits_a_description_the_request_did_not_supply(self):
        with _wired() as (_table, audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "mfaRequired": True})
            )

        details = audit.call_args.args[2]
        assert "description" not in details, (
            "the audit entry records a description for a request that never supplied one "
            f"({details.get('description')!r}), so the log reads as though the text had been "
            "changed to that value"
        )
        assert details["roleName"] == _ROLE
        assert details["operation"] == "update"

    def test_the_audit_entry_records_a_description_the_request_did_supply(self):
        with _wired() as (_table, audit):
            createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert audit.call_args.args[2]["description"] == "new text"


@pytest.mark.unit
class TestCreateStillRequiresADescription:
    """POST /roles was deliberately left alone, so the relaxation must not have leaked into it."""

    def test_creating_a_role_without_a_description_is_still_a_400(self):
        with _wired(stored=None) as (table, _audit):
            response = createRole.handle_post_request(
                {
                    "requestContext": {"http": {"method": "POST", "path": "/roles"}},
                    "pathParameters": None,
                    "queryStringParameters": None,
                    "body": json.dumps({"roleName": "brand-new-role"}),
                    "headers": {"authorization": "Bearer test-token"},
                }
            )

        assert response["statusCode"] == 400, (
            f"a role was created with no description: {response}"
        )
        assert "description" in json.loads(response["body"])["message"]

    def test_the_create_model_still_declares_description_as_required(self):
        from backend.backend.models.roleConstraints import (
            CreateRoleRequestModel,
            UpdateRoleRequestModel,
        )

        assert CreateRoleRequestModel.__fields__["description"].required is True
        assert UpdateRoleRequestModel.__fields__["description"].required is False


@pytest.mark.unit
class TestDenialStillSurfacesAs403:
    """Guards the restructured handler; the full matrix is in
    test_createRole_authz_fail_closed.py."""

    def test_a_denied_update_is_403_and_writes_nothing(self):
        with _wired(denied_actions=("PUT",)) as (table, _audit):
            response = createRole.handle_put_request(
                _put_event({"roleName": _ROLE, "description": "new text"})
            )

        assert response["statusCode"] == 403, (
            f"a Tier-2 denial surfaced as {response['statusCode']}: {response}"
        )
        assert table.update_calls == []
        assert table.item["description"] == "original text"
