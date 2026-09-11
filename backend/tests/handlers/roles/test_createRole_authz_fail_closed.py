# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST/PUT /roles: object-level denial denies, and reaches the caller as 403.

`create_role` and `update_role` both evaluate a role object against Casbin before writing.
Two properties are pinned per site.

**Empty token list denies before the enforcer is consulted.** With no authenticated identity
there is nothing to evaluate against, so the write is refused up front — backend/CLAUDE.md
Rule 4 for a single-resource check. The assertion is that `CasbinEnforcer` was never
constructed, not merely that the response was a 403: the enforcer injected here is a
stand-in whose verdict the test chooses, so "the response was 403" alone can be true for the
wrong reason.

**A denial is a denial, not a 500.** A denial signalled by *returning* a completed
`authorization_error()` response from a business function is indistinguishable, at the call
site, from the response model the function returns on success — the caller reads
`result.roleName`, gets `AttributeError` on a dict, and the broad `except Exception` turns a
routine permission refusal into an internal server error. The functions therefore raise
instead: one return type, and a denial the handler translates explicitly. The status-code
assertions below are what keeps that true.

Each denial case is paired with the permitted case for the same site, because a handler that
refused everything would satisfy every "denied" assertion on its own.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.roles import createRole


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


def _authorized_triples(spy):
    """The (action, object__type, roleName) triples handed to Casbin, as a set.

    A set, deliberately: the property is that the right object was authorized with the right
    action, not that it happened exactly once or in a particular order. A handler made strictly
    safer -- evaluating an extra object, or the same one twice -- must not turn these
    assertions red, while a handler that drops a check goes red because the triple disappears.
    """
    return {
        (call["action"], call["object"].get("object__type"), call["object"].get("roleName"))
        for call in spy.calls
    }


# (site id, HTTP method, request handler name, business function name, enforced action)
_SITES = [
    ("create_role", "POST", "handle_post_request", "create_role", "POST"),
    ("update_role", "PUT", "handle_put_request", "update_role", "PUT"),
]
_SITE_IDS = [site[0] for site in _SITES]


def _event(method):
    return {
        "requestContext": {"http": {"method": method, "path": "/roles"}},
        "pathParameters": None,
        "queryStringParameters": None,
        "body": json.dumps({"roleName": _ROLE, "description": "a role"}),
        "headers": {"authorization": "Bearer test-token"},
    }


def _wire(tokens=("tester",), denied_actions=()):
    spy = _EnforcerSpy(denied_actions=denied_actions)
    roles_table = MagicMock()

    saved_claims = createRole.claims_and_roles
    patches = [
        patch.object(createRole, "CasbinEnforcer", spy.factory),
        patch.object(createRole, "roles_table", roles_table),
        patch.object(createRole, "log_auth_changes", MagicMock()),
        # `common.dynamodb` is a MagicMock under this harness, so the name the handler bound
        # at import time returns a mock that cannot be unpacked into the three-tuple the
        # update path expects. What the expression contains is asserted in
        # test_createRole_partial_update.py; here it only has to be well formed.
        patch.object(
            createRole,
            "to_update_expr",
            MagicMock(return_value=({"#f0": "description"}, {":v0": "d"}, "SET #f0 = :v0")),
        ),
    ]
    for p in patches:
        p.start()
    createRole.claims_and_roles = {"tokens": list(tokens)}

    def _undo():
        createRole.claims_and_roles = saved_claims
        for p in reversed(patches):
            p.stop()

    return spy, roles_table, _undo


@pytest.mark.unit
class TestEmptyTokenListDenies:
    """Rule 4, single resource: no identity means deny before the enforcer is consulted."""

    @pytest.mark.parametrize(
        "site,method,handler_name,function_name,action", _SITES, ids=_SITE_IDS
    )
    def test_empty_tokens_return_403_without_consulting_casbin(
        self, site, method, handler_name, function_name, action
    ):
        spy, roles_table, undo = _wire(tokens=())
        try:
            response = getattr(createRole, handler_name)(_event(method))
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: an unauthenticated request returned {response['statusCode']} "
            f"instead of 403: {response}"
        )
        assert spy.constructions == [], (
            f"{site}: CasbinEnforcer was constructed for an empty token list; with no "
            f"identity the write must be refused before authorization is evaluated"
        )
        assert spy.calls == [], f"{site}: enforce() ran with no identity: {spy.calls}"

    @pytest.mark.parametrize(
        "site,method,handler_name,function_name,action", _SITES, ids=_SITE_IDS
    )
    def test_empty_tokens_write_nothing(
        self, site, method, handler_name, function_name, action
    ):
        spy, roles_table, undo = _wire(tokens=())
        try:
            getattr(createRole, handler_name)(_event(method))
        finally:
            undo()

        roles_table.put_item.assert_not_called()
        roles_table.update_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,method,handler_name,function_name,action", _SITES, ids=_SITE_IDS
    )
    def test_the_business_function_itself_denies(
        self, site, method, handler_name, function_name, action
    ):
        """Called directly, with Tier 1 out of the picture entirely."""
        spy, roles_table, undo = _wire(tokens=())
        try:
            with pytest.raises(createRole.AuthorizationDenied):
                getattr(createRole, function_name)(
                    {"roleName": _ROLE, "description": "a role"}, {"tokens": []}
                )
        finally:
            undo()

        assert spy.constructions == [], f"{site}: enforcer constructed with no identity"
        roles_table.put_item.assert_not_called()
        roles_table.update_item.assert_not_called()


@pytest.mark.unit
class TestDenialSurfacesAs403NotAs500:
    """A Tier-2 refusal is a documented 403; it must not arrive as an internal error."""

    @pytest.mark.parametrize(
        "site,method,handler_name,function_name,action", _SITES, ids=_SITE_IDS
    )
    def test_a_denied_caller_gets_403(
        self, site, method, handler_name, function_name, action
    ):
        spy, roles_table, undo = _wire(denied_actions=(action,))
        try:
            response = getattr(createRole, handler_name)(_event(method))
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: a Tier-2 denial surfaced as {response['statusCode']}; a denial "
            f"returned as a response dict and then dereferenced as a model becomes a 500: "
            f"{response}"
        )
        assert json.loads(response["body"])["message"] == "Not Authorized"
        roles_table.put_item.assert_not_called()
        roles_table.update_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,method,handler_name,function_name,action", _SITES, ids=_SITE_IDS
    )
    def test_the_denial_was_decided_on_the_role_being_written(
        self, site, method, handler_name, function_name, action
    ):
        """The verdict is only meaningful if the object carries the fields that scope it."""
        spy, roles_table, undo = _wire(denied_actions=(action,))
        try:
            getattr(createRole, handler_name)(_event(method))
        finally:
            undo()

        assert (action, "role", _ROLE) in _authorized_triples(spy), (
            f"{site}: the role being written was never evaluated for {action}; the Casbin "
            f"matcher compares the action for equality. Evaluated: "
            f"{sorted(_authorized_triples(spy))}"
        )


@pytest.mark.unit
class TestPermittedCallerStillWrites:
    """Positive control: every denial assertion above is also satisfied by a broken deny-all."""

    def test_create_succeeds(self):
        spy, roles_table, undo = _wire()
        try:
            response = createRole.handle_post_request(_event("POST"))
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized role create was refused: {response}"
        )
        roles_table.put_item.assert_called_once()
        assert roles_table.put_item.call_args.kwargs["Item"]["roleName"] == _ROLE
        assert ("POST", "role", _ROLE) in _authorized_triples(spy), (
            f"the write went through without authorizing the role: "
            f"{sorted(_authorized_triples(spy))}"
        )

    def test_update_succeeds(self):
        spy, roles_table, undo = _wire()
        try:
            response = createRole.handle_put_request(_event("PUT"))
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized role update was refused: {response}"
        )
        roles_table.update_item.assert_called_once()
        assert roles_table.update_item.call_args.kwargs["Key"] == {"roleName": _ROLE}
        assert ("PUT", "role", _ROLE) in _authorized_triples(spy), (
            f"the write went through without authorizing the role: "
            f"{sorted(_authorized_triples(spy))}"
        )


@pytest.mark.unit
class TestTier1StillDeniesEmptyTokens:
    """The upstream control that masks the Tier-2 defect in production stays in place."""

    @pytest.mark.parametrize("method", ["POST", "PUT"])
    def test_lambda_handler_denies_an_empty_token_list(self, method):
        spy = _EnforcerSpy()
        with patch.object(createRole, "CasbinEnforcer", spy.factory), patch.object(
            createRole, "request_to_claims", MagicMock(return_value={"tokens": []})
        ), patch.object(createRole, "roles_table", MagicMock()) as roles_table:
            response = createRole.lambda_handler(_event(method), MagicMock())

        assert response["statusCode"] == 403
        assert spy.constructions == [], (
            f"Tier 1 constructed an enforcer for an empty token list: {spy.constructions}"
        )
        roles_table.put_item.assert_not_called()
        roles_table.update_item.assert_not_called()
