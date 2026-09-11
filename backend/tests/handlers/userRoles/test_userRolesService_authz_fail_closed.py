# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""User-role assignment writes: object-level denial denies, and reaches the caller as 403.

Six separate Tier-2 checks guard the assignment writes: the set-level check
(`authorize_user_role_set_operation`) on the two whole-set operations PUT and DELETE, one in
`create_user_roles`, two in `update_user_roles` (every row the request names, and every row it
drops), and one in `delete_user_roles`. Each is exercised on its own below, because they are
independent code paths that happen to look alike; `update_user_roles` in particular can be
denied on the remove half while the add half is permitted, which is the case a single test of
"PUT is denied" would never reach.

POST has no set-level check and must not grow one: its body must name at least one role, so its
per-named-row loop always evaluates at least one object whatever the target holds, and POST can
only create the rows it names. A set-level object carries no `roleName`, so the enforcer's empty
placeholder cannot match a roleName-scoped rule -- adding the check there would deny such a
caller the very assignments they are scoped to while closing nothing. Both halves of that
decision live in test_userRolesService_post_scoped_grant.py.

Every assertion about what was authorized is made against the SET of (action, roleName)
pairs handed to Casbin -- never a call count or a position in the sequence -- so a handler
that authorizes more than the minimum stays green while a handler that drops a check goes
red. For the same reason the enforcer stand-in refuses one named (action, roleName) pair
rather than an action outright: a stand-in that refuses everything cannot tell "the site
under test denied" from "an earlier, broader check denied first".

**Empty token list denies before the enforcer is consulted.** With no authenticated identity
there is nothing to evaluate, so the write is refused up front -- backend/CLAUDE.md Rule 4 for
a single-resource check. The check is hoisted above every loop so an empty `roleName` set (or
a user with no existing assignments) cannot skip it by iterating zero times. The assertion is
that `CasbinEnforcer` was never constructed, which is the actual property; "the response was
403" alone can hold for the wrong reason, since the enforcer injected here is a stand-in whose
verdict the test chooses.

**A denial is a denial, not a 500.** A denial signalled by *returning* a completed
`authorization_error()` response from a business function is indistinguishable at the call
site from the model the function returns on success -- the caller reads `result.userId`, gets
`AttributeError` on a dict, and the broad `except Exception` reports an internal server error
for an ordinary permission refusal. The functions therefore raise, and the request handler
translates that to a 403.

**No answer that depends on the target's membership is given before authorization.** Three
channels carried that leak and each has its own class below: a differential update whose change
set is empty (`TestANoOpUpdateIsStillAuthorized`), a create whose named role the user already
holds (`TestCreateDoesNotDiscloseMembership`), and a delete-all against a user holding nothing
(`TestDeleteAllIsAuthorizedAndTruthful`). In every case the endpoint answered a caller
authorized for no userRole row at all, and only the correct guess answered 200.

Each denial case is paired with the permitted case for the same site, because a handler that
refused everything would satisfy every "denied" assertion on its own.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.userRoles import userRolesService


_USER = "tester"
_OLD_ROLE = "old-role"
_NEW_ROLE = "new-role"
#: A role the user is assigned to but whose roles-table row no longer exists.
_ORPHAN_ROLE = "orphaned-role"


class _EnforcerSpy:
    """A CasbinEnforcer stand-in that records every construction and every enforce call.

    `deny` is a predicate over (object, action) rather than a single verdict, so a test can
    refuse exactly the check it is measuring and leave every other check permitted. A stand-in
    that refuses an action outright would be short-circuited by the operation-level check that
    now runs first, and the per-row check under test would never execute.
    """

    def __init__(self, deny=None, denied_actions=()):
        denied_actions = set(denied_actions)
        self.deny = deny or (lambda obj, action: action in denied_actions)
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
                return not spy.deny(obj, action)

            def enforceAPI(self, event):
                return True

        return _Enforcer


def _deny_pair(action, role_name):
    """Refuse exactly one (action, roleName) evaluation and permit everything else."""
    def _deny(obj, act):
        return act == action and obj.get("roleName") == role_name
    return _deny


def _deny_everything(obj, action):
    return True


# (site id, HTTP method, request handler, existing assignments, request body,
#  enforced action, the roleName that site's check evaluates)
_SITES = [
    (
        "operation.update",
        "PUT",
        "handle_put_request",
        (_OLD_ROLE,),
        {"userId": _USER, "roleName": [_NEW_ROLE]},
        "PUT",
        None,
    ),
    (
        "operation.delete",
        "DELETE",
        "handle_delete_request",
        (_OLD_ROLE,),
        {"userId": _USER},
        "DELETE",
        None,
    ),
    (
        "create_user_roles",
        "POST",
        "handle_post_request",
        (),
        {"userId": _USER, "roleName": [_NEW_ROLE]},
        "POST",
        _NEW_ROLE,
    ),
    (
        "update_user_roles.add",
        "PUT",
        "handle_put_request",
        (_OLD_ROLE,),
        {"userId": _USER, "roleName": [_NEW_ROLE]},
        "POST",
        _NEW_ROLE,
    ),
    (
        "update_user_roles.remove",
        "PUT",
        "handle_put_request",
        (_OLD_ROLE,),
        {"userId": _USER, "roleName": [_NEW_ROLE]},
        "DELETE",
        _OLD_ROLE,
    ),
    (
        "delete_user_roles",
        "DELETE",
        "handle_delete_request",
        (_OLD_ROLE,),
        {"userId": _USER},
        "DELETE",
        _OLD_ROLE,
    ),
]
_SITE_IDS = [site[0] for site in _SITES]


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
            "userId": {"S": _USER},
            "roleName": {"S": role},
            "createdOn": {"S": "2026-01-01T00:00:00"},
        }
        for role in role_names
    ]


def _authorized_pairs(spy):
    """The (action, roleName) pairs handed to Casbin, as a set.

    A set, deliberately: the property is that the right object was authorized with the right
    action, not that it happened a particular number of times or in a particular order. A
    handler made strictly safer -- evaluating an extra object, or the same one twice -- must
    not turn these assertions red. The operation-level check carries no roleName, so it appears
    as (action, None).
    """
    return {(call["action"], call["object"].get("roleName")) for call in spy.calls}


def _wire(existing=(), tokens=(_USER,), deny=None, denied_actions=(), known_roles=None):
    spy = _EnforcerSpy(deny=deny, denied_actions=denied_actions)
    user_roles_table = MagicMock()
    batch = MagicMock()
    user_roles_table.batch_writer.return_value.__enter__.return_value = batch
    audit = MagicMock()

    def _get_role(role):
        if known_roles is not None and role not in known_roles:
            return []
        return [{"roleName": {"S": role}}]

    saved_claims = userRolesService.claims_and_roles
    patches = [
        patch.object(userRolesService, "CasbinEnforcer", spy.factory),
        patch.object(userRolesService, "user_roles_table", user_roles_table),
        patch.object(userRolesService, "log_auth_changes", audit),
        patch.object(
            userRolesService,
            "get_all_roles_for_user",
            MagicMock(return_value=_typed_assignments(existing)),
        ),
        # Every referenced role exists unless a test names the ones that do; role existence is
        # not what most of these tests measure.
        patch.object(userRolesService, "get_role", MagicMock(side_effect=_get_role)),
    ]
    for p in patches:
        p.start()
    userRolesService.claims_and_roles = {"tokens": list(tokens)}

    def _undo():
        userRolesService.claims_and_roles = saved_claims
        for p in reversed(patches):
            p.stop()

    return spy, user_roles_table, batch, audit, _undo


@pytest.mark.unit
class TestEmptyTokenListDenies:
    """Rule 4, single resource: no identity means deny before the enforcer is consulted."""

    @pytest.mark.parametrize(
        "site,method,handler_name,existing,body,action,role", _SITES, ids=_SITE_IDS
    )
    def test_empty_tokens_return_403_without_consulting_casbin(
        self, site, method, handler_name, existing, body, action, role
    ):
        spy, user_roles_table, batch, audit, undo = _wire(existing=existing, tokens=())
        try:
            response = getattr(userRolesService, handler_name)(_event(method, body))
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
        "site,method,handler_name,existing,body,action,role", _SITES, ids=_SITE_IDS
    )
    def test_empty_tokens_write_nothing(
        self, site, method, handler_name, existing, body, action, role
    ):
        spy, user_roles_table, batch, audit, undo = _wire(existing=existing, tokens=())
        try:
            getattr(userRolesService, handler_name)(_event(method, body))
        finally:
            undo()

        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    def test_an_empty_role_set_cannot_iterate_past_the_check(self):
        """The guard sits above the loop, so zero iterations still deny.

        `delete_user_roles` loops over the user's existing assignments; a user with none
        would run the loop body zero times and reach the batch write with no authorization
        evaluated at all.
        """
        spy, user_roles_table, batch, audit, undo = _wire(existing=(), tokens=())
        try:
            response = userRolesService.handle_delete_request(
                _event("DELETE", {"userId": _USER})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"a user with no existing assignments skipped the authorization check "
            f"entirely: {response}"
        )
        assert spy.constructions == []


@pytest.mark.unit
class TestDenialSurfacesAs403NotAs500:
    """A Tier-2 refusal is a documented 403; it must not arrive as an internal error."""

    @pytest.mark.parametrize(
        "site,method,handler_name,existing,body,action,role", _SITES, ids=_SITE_IDS
    )
    def test_a_denied_caller_gets_403(
        self, site, method, handler_name, existing, body, action, role
    ):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=existing, deny=_deny_pair(action, role)
        )
        try:
            response = getattr(userRolesService, handler_name)(_event(method, body))
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: a Tier-2 denial surfaced as {response['statusCode']}; a denial "
            f"returned as a response dict and then dereferenced as a model becomes a 500: "
            f"{response}"
        )
        assert json.loads(response["body"])["message"] == "Not Authorized"
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,method,handler_name,existing,body,action,role", _SITES, ids=_SITE_IDS
    )
    def test_this_sites_check_ran_on_a_userRole_object(
        self, site, method, handler_name, existing, body, action, role
    ):
        """The row this site guards was evaluated for this site's action.

        Asserted as membership of the evaluated (action, roleName) set rather than as a call
        count or a position in the sequence: a handler that authorizes more than the minimum
        is strictly safer and must stay green, while a handler that drops this site's check
        goes red because the pair disappears.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=existing, deny=_deny_pair(action, role)
        )
        try:
            getattr(userRolesService, handler_name)(_event(method, body))
        finally:
            undo()

        assert (action, role) in _authorized_pairs(spy), (
            f"{site}: ({action}, {role}) was never evaluated. Evaluated: "
            f"{sorted(_authorized_pairs(spy), key=str)}"
        )
        for call in spy.calls:
            assert call["object"]["object__type"] == "userRole"
            assert call["object"]["userId"] == _USER

    def test_both_halves_of_a_differential_update_are_gated(self):
        """Control for the pairing above: the add half and the remove half are both evaluated.

        With only the remove half denied, the add half must have been evaluated and permitted,
        which is what a single test of "PUT is denied" would never reach.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_OLD_ROLE,), deny=_deny_pair("DELETE", _OLD_ROLE)
        )
        try:
            userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        pairs = _authorized_pairs(spy)
        assert {("POST", _NEW_ROLE), ("DELETE", _OLD_ROLE)} <= pairs, (
            f"a differential update left one half ungated; evaluated: "
            f"{sorted(pairs, key=str)}"
        )


@pytest.mark.unit
class TestTheWholeSetOperationItselfIsAuthorized:
    """PUT and DELETE evaluate the role set as a whole, from the request body alone.

    Those two operations can change rows the request does not name -- PUT deletes every
    assignment absent from the body, DELETE removes all of them -- and their per-row checks are
    derived from state the caller cannot see, so on their own they leave a request that resolves
    to no change evaluating nothing. The set-level object carries the userId the request names
    and no roleName, so it is evaluated for every request whatever the target holds.

    POST is deliberately absent: it can only create the rows it names, and it must name at least
    one, so its per-row loop is already unconditional. See
    test_userRolesService_post_scoped_grant.py.
    """

    @pytest.mark.parametrize(
        "method,handler_name,existing,body,action",
        [
            ("PUT", "handle_put_request", (_OLD_ROLE,), {"userId": _USER, "roleName": [_NEW_ROLE]}, "PUT"),
            ("DELETE", "handle_delete_request", (_OLD_ROLE,), {"userId": _USER}, "DELETE"),
        ],
        ids=["update", "delete"],
    )
    def test_the_set_level_object_is_evaluated(
        self, method, handler_name, existing, body, action
    ):
        spy, user_roles_table, batch, audit, undo = _wire(existing=existing)
        try:
            response = getattr(userRolesService, handler_name)(_event(method, body))
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert (action, None) in _authorized_pairs(spy), (
            f"the {method} operation was never authorized on the role set as a whole; "
            f"evaluated: {sorted(_authorized_pairs(spy), key=str)}"
        )
        operation_calls = [
            call for call in spy.calls
            if call["action"] == action and "roleName" not in call["object"]
        ]
        for call in operation_calls:
            assert call["object"]["userId"] == _USER
            assert call["object"]["object__type"] == "userRole"

    @pytest.mark.parametrize(
        "method,handler_name,existing,body,deny",
        [
            # POST has no set-level object, so the check that must precede the membership read
            # is the per-named-row one.
            ("POST", "handle_post_request", (), {"userId": _USER, "roleName": [_NEW_ROLE]},
             lambda obj, act: obj.get("roleName") == _NEW_ROLE),
            ("PUT", "handle_put_request", (_OLD_ROLE,), {"userId": _USER, "roleName": [_NEW_ROLE]},
             lambda obj, act: "roleName" not in obj),
            ("DELETE", "handle_delete_request", (_OLD_ROLE,), {"userId": _USER},
             lambda obj, act: "roleName" not in obj),
        ],
        ids=["create", "update", "delete"],
    )
    def test_a_refused_request_reads_nothing_and_writes_nothing(
        self, method, handler_name, existing, body, deny
    ):
        """The first authorization verdict precedes the membership read on every write path.

        The negative half is paired with the permitted case below it, which reads the same
        stand-in through the same wiring: without that control, "the reader was not called"
        would also hold for a test that never reached the handler at all.
        """
        outcomes = {}
        for label, deny_predicate in (("refused", deny), ("permitted", None)):
            spy, user_roles_table, batch, audit, undo = _wire(
                existing=existing, deny=deny_predicate
            )
            membership_reader = MagicMock(return_value=_typed_assignments(existing))
            try:
                with patch.object(
                    userRolesService, "get_all_roles_for_user", membership_reader
                ):
                    response = getattr(userRolesService, handler_name)(_event(method, body))
            finally:
                undo()
            outcomes[label] = (response["statusCode"], membership_reader.called)

            if label == "refused":
                batch.put_item.assert_not_called()
                batch.delete_item.assert_not_called()

        assert outcomes["refused"] == (403, False), (
            f"a refused {method} read the target's membership before the first authorization "
            f"verdict: {outcomes}"
        )
        assert outcomes["permitted"][1] is True, (
            f"the control never reached the membership read either, so the negative above "
            f"proves nothing: {outcomes}"
        )

    def test_post_is_not_authorized_against_a_set_level_object(self):
        """The Defect-2 decision, pinned: POST evaluates named rows only.

        A set-level object carries no `roleName`, so the enforcer substitutes its empty
        placeholder and a roleName-scoped rule cannot match it. Adding such a check to POST
        would take away that caller's ability to grant the assignment they are scoped to. This
        asserts the shape (no roleName-less POST evaluation); the capability it protects is
        measured against the real enforcer in test_userRolesService_post_scoped_grant.py.
        """
        spy, user_roles_table, batch, audit, undo = _wire(existing=())
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        roleless = [call for call in spy.calls if "roleName" not in call["object"]]
        assert roleless == [], (
            f"POST evaluated a userRole object with no roleName, which no roleName-scoped "
            f"constraint can match: {roleless}"
        )
        assert ("POST", _NEW_ROLE) in _authorized_pairs(spy), (
            f"POST authorized nothing at all; evaluated: "
            f"{sorted(_authorized_pairs(spy), key=str)}"
        )


@pytest.mark.unit
class TestPermittedCallerStillWrites:
    """Positive control: every denial assertion above is also satisfied by a broken deny-all."""

    def test_create_succeeds(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=())
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized assignment create was refused: {response}"
        )
        assert batch.put_item.call_args.kwargs["Item"]["roleName"] == _NEW_ROLE

    def test_update_succeeds(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_OLD_ROLE,))
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized assignment update was refused: {response}"
        )
        assert batch.put_item.call_args.kwargs["Item"]["roleName"] == _NEW_ROLE
        assert batch.delete_item.call_args.kwargs["Key"] == {
            "userId": _USER,
            "roleName": _OLD_ROLE,
        }

    def test_delete_succeeds(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_OLD_ROLE,))
        try:
            response = userRolesService.handle_delete_request(
                _event("DELETE", {"userId": _USER})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an authorized assignment delete was refused: {response}"
        )
        assert batch.delete_item.call_args.kwargs["Key"] == {
            "userId": _USER,
            "roleName": _OLD_ROLE,
        }


@pytest.mark.unit
class TestANoOpUpdateIsStillAuthorized:
    """A differential update whose change set is empty must not answer without authorizing.

    `update_user_roles` computes what to add and what to remove and authorizes each. When the
    target user already holds exactly the roles the request names, both differential sets are
    empty: every loop iterates zero times, the hoisted empty-token guard passes because the
    caller *is* authenticated, and the request is answered with nothing evaluated at all. That
    turns the endpoint into a membership oracle -- a caller refused on every userRole row still
    learns the user's exact role set, because only the correct set answers 200.

    The property is therefore that the operation and the rows the caller NAMED are authorized,
    whether or not they changed. Asserted as the evaluated (action, roleName) set, so
    authorizing more than the minimum stays green.
    """

    def test_a_no_op_update_authorizes_the_operation_and_the_named_rows(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_OLD_ROLE,))
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_OLD_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert {("PUT", None), ("POST", _OLD_ROLE)} <= _authorized_pairs(spy), (
            f"a no-op update was answered without authorizing the operation and the row the "
            f"caller named; evaluated: {sorted(_authorized_pairs(spy), key=str)}"
        )

    def test_a_caller_denied_on_every_row_gets_403_for_a_no_op(self):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_OLD_ROLE,), deny=_deny_everything
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_OLD_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"a caller authorized for no userRole row was served a no-op update: {response}"
        )
        assert spy.calls, "the refusal came from somewhere other than an authorization check"
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    def test_a_denied_caller_cannot_tell_a_correct_guess_from_a_wrong_one(self):
        """The oracle itself: the response must not depend on the user's actual roles."""
        statuses = {}
        for label, named_roles in (
            ("correct guess", [_OLD_ROLE]),
            ("wrong guess", [_NEW_ROLE]),
            ("superset guess", [_OLD_ROLE, _NEW_ROLE]),
        ):
            spy, user_roles_table, batch, audit, undo = _wire(
                existing=(_OLD_ROLE,), deny=_deny_everything
            )
            try:
                statuses[label] = userRolesService.handle_put_request(
                    _event("PUT", {"userId": _USER, "roleName": named_roles})
                )["statusCode"]
            finally:
                undo()

        assert set(statuses.values()) == {403}, (
            f"the response distinguishes the user's actual roles from a wrong guess, so a "
            f"caller authorized for nothing can read role membership off the status code: "
            f"{statuses}"
        )

    def test_a_no_op_does_not_write_and_does_not_claim_a_change(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_OLD_ROLE,))
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_OLD_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()
        assert (
            json.loads(response["body"])["message"]
            == userRolesService.USER_ROLE_UPDATE_NO_CHANGES_MESSAGE
        ), f"a no-op reported a role change to the caller: {response}"

    def test_the_audit_record_says_whether_anything_changed(self):
        """A false "roles updated" entry is its own defect: the trail must be truthful.

        Asserted over every recorded call rather than by pinning the call count: an
        implementation that also recorded the attempt, or recorded per row, would be no less
        truthful, and the property under test is what the records SAY.
        """
        for label, existing, named_roles, expected_changed in (
            ("no-op", (_OLD_ROLE,), [_OLD_ROLE], False),
            ("real change", (_OLD_ROLE,), [_NEW_ROLE], True),
        ):
            spy, user_roles_table, batch, audit, undo = _wire(existing=existing)
            try:
                response = userRolesService.handle_put_request(
                    _event("PUT", {"userId": _USER, "roleName": named_roles})
                )
            finally:
                undo()

            assert response["statusCode"] == 200, f"{label}: {response}"
            assert audit.call_args_list, f"{label}: no audit record was written at all"
            recorded_changes = [call.args[2].get("changed") for call in audit.call_args_list]
            assert set(recorded_changes) == {expected_changed}, (
                f"{label}: the audit records say changed={recorded_changes}"
            )

    def test_an_authorized_real_change_is_unaffected(self):
        """Positive control: the added named-row check must not block an ordinary update."""
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_OLD_ROLE,))
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_OLD_ROLE, _NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        written = [call.kwargs["Item"]["roleName"] for call in batch.put_item.call_args_list]
        assert written == [_NEW_ROLE], (
            f"a role the user already held was rewritten, or the added one was not: {written}"
        )
        batch.delete_item.assert_not_called()


@pytest.mark.unit
class TestCreateDoesNotDiscloseMembership:
    """POST answered "one or more roles already exist" before authorizing anything.

    `is_any_user_role_already_existing` is a membership test, so deciding it first gave a caller
    authorized for no userRole row a 400 for a role the target holds and a 403 for one they do
    not -- the same oracle the differential update carried, in the create path. Role existence
    is the same kind of answer and is likewise decided after the verdicts.
    """

    @pytest.mark.parametrize(
        "label,existing",
        [("target holds the named role", (_NEW_ROLE,)), ("target holds nothing", ())],
    )
    def test_a_denied_caller_is_refused_either_way(self, label, existing):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=existing, deny=_deny_everything
        )
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{label}: a caller authorized for no userRole row was told whether the "
            f"assignment already exists: {response}"
        )
        batch.put_item.assert_not_called()

    def test_the_already_exists_rejection_still_reaches_an_authorized_caller(self):
        """Positive control: the duplicate check must still work for a permitted caller."""
        spy, user_roles_table, batch, audit, undo = _wire(existing=(_NEW_ROLE,))
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 400, response
        assert "already exist" in json.loads(response["body"])["message"]
        batch.put_item.assert_not_called()

    def test_an_unknown_role_is_not_disclosed_to_a_denied_caller(self):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(), deny=_deny_everything, known_roles=()
        )
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"a caller authorized for no userRole row learned that the named role does not "
            f"exist: {response}"
        )

    def test_an_unknown_role_still_rejects_an_authorized_caller(self):
        """Positive control for the ordering above, and Rule 11 on the message."""
        spy, user_roles_table, batch, audit, undo = _wire(existing=(), known_roles=())
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 400, response
        message = json.loads(response["body"])["message"]
        assert _NEW_ROLE not in message, (
            f"the response echoes the role name the caller submitted: {message}"
        )
        assert userRolesService.ROLE_DOES_NOT_EXIST_MESSAGE in message, (
            f"the caller is not told that a named role is unknown: {message}"
        )
        batch.put_item.assert_not_called()


@pytest.mark.unit
class TestUpdateRoleExistenceCoversOnlyAddedRoles:
    """The rule: a named role must exist unless the target already holds it.

    A roles-table row can be deleted while user assignments still reference it, so ORPHANED
    assignments exist on upgraded deployments. PUT is a whole-set replace and the Edit form
    pre-populates from the user's current roles, so requiring every NAMED role to exist made
    such a user un-editable -- every save, including the one that removes the orphan, 400ed on
    the orphan itself, and the Rule 11 message cannot say which name it was. Existence is
    therefore required only of the roles the request would ADD.

    The authorization verdicts stay independent of the target's membership, which is what closed
    the oracle: the set-level check and every per-named-row check run before the existence check,
    so `test_the_403_does_not_depend_on_membership_or_existence` below still holds. Only the 400
    now distinguishes "adding an unknown role" from "keeping one you already have" -- and that is
    the distinction an operator needs in order to repair the orphan at all.
    """

    def test_adding_an_unknown_role_is_rejected(self):
        """A role the target does not hold must resolve in the roles table."""
        spy, user_roles_table, batch, audit, undo = _wire(existing=(), known_roles=())
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 400, (
            f"a role that does not exist was granted to a user who did not hold it: {response}"
        )
        message = json.loads(response["body"])["message"]
        assert _NEW_ROLE not in message, (
            f"the response echoes the role name the caller submitted: {message}"
        )
        assert userRolesService.ROLE_DOES_NOT_EXIST_MESSAGE in message, (
            f"the caller is not told that a named role is unknown: {message}"
        )
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    def test_retaining_an_orphaned_assignment_is_accepted(self):
        """The over-tightening: the pre-populated Edit form re-sends the orphan.

        The target holds a role whose roles-table row is gone. A full-list PUT that names it
        must succeed, otherwise the assignment can never be edited at all.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_ORPHAN_ROLE,), known_roles=(_OLD_ROLE, _NEW_ROLE)
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_ORPHAN_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"re-sending an orphaned assignment was rejected, so a user holding one cannot be "
            f"edited at all: {response}"
        )
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    def test_an_orphaned_assignment_can_be_removed_end_to_end(self):
        """The repair path: drop the orphan from the list and it is deleted.

        This shape is accepted under the strict rule too -- every name that REMAINS in the body
        resolves -- so it documents the repair rather than ratcheting the relaxed rule. The
        removal that needs the relaxed rule is the one below it.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_OLD_ROLE, _ORPHAN_ROLE), known_roles=(_OLD_ROLE, _NEW_ROLE)
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_OLD_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"removing an orphaned assignment was rejected: {response}"
        )
        deleted = {call.kwargs["Key"]["roleName"] for call in batch.delete_item.call_args_list}
        assert deleted == {_ORPHAN_ROLE}, (
            f"the orphaned assignment was not the row deleted: {deleted}"
        )
        batch.put_item.assert_not_called()
        assert ("DELETE", _ORPHAN_ROLE) in _authorized_pairs(spy), (
            f"the removal was not authorized as a userRole DELETE; evaluated: "
            f"{sorted(_authorized_pairs(spy), key=str)}"
        )

    def test_a_role_can_be_removed_from_a_user_who_also_holds_an_orphan(self):
        """The removal the strict rule blocked outright, and the one that ratchets the fix.

        The target holds a live role AND an orphan. Taking the live role away leaves the orphan in
        the body -- the Edit form pre-populates from the user's current roles, so the name of a
        role whose roles-table row is gone is still submitted -- and requiring every NAMED role to
        exist rejects the save before anything is deleted. The role cannot be removed by keeping
        the orphan (this test) and cannot be removed by dropping it either, since that is a
        separate edit; the user's role set is frozen until the orphan is repaired out of band.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_ORPHAN_ROLE, _OLD_ROLE), known_roles=(_OLD_ROLE, _NEW_ROLE)
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_ORPHAN_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"a live role could not be removed from a user who also holds an orphaned "
            f"assignment, because the request retains the orphan's name: {response}"
        )
        deleted = {call.kwargs["Key"]["roleName"] for call in batch.delete_item.call_args_list}
        assert deleted == {_OLD_ROLE}, (
            f"the row the request dropped was not the row deleted: {deleted}"
        )
        batch.put_item.assert_not_called()
        assert ("DELETE", _OLD_ROLE) in _authorized_pairs(spy), (
            f"the removal was not authorized as a userRole DELETE; evaluated: "
            f"{sorted(_authorized_pairs(spy), key=str)}"
        )

    def test_a_real_addition_alongside_a_retained_orphan_still_lands(self):
        """The other half of the same edit: adding a role while keeping the orphan."""
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_ORPHAN_ROLE,), known_roles=(_NEW_ROLE,)
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_ORPHAN_ROLE, _NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"an addition was refused because the retained role no longer exists: {response}"
        )
        written = [call.kwargs["Item"]["roleName"] for call in batch.put_item.call_args_list]
        assert written == [_NEW_ROLE], (
            f"the added role was not written, or the retained orphan was rewritten: {written}"
        )
        batch.delete_item.assert_not_called()

    def test_a_denied_caller_is_refused_before_role_existence_is_answered(self):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(), deny=_deny_everything, known_roles=()
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"a caller authorized for no userRole row learned that the named role does not "
            f"exist: {response}"
        )

    @pytest.mark.parametrize(
        "label,existing,known_roles",
        [
            ("target holds it, role exists", (_NEW_ROLE,), (_NEW_ROLE,)),
            ("target holds it, role deleted", (_NEW_ROLE,), ()),
            ("target does not hold it, role exists", (), (_NEW_ROLE,)),
            ("target does not hold it, role deleted", (), ()),
        ],
    )
    def test_the_403_does_not_depend_on_membership_or_existence(
        self, label, existing, known_roles
    ):
        """The oracle stays closed: the authorization verdict is a function of the request.

        The relaxed existence rule reads the target's membership, so this pins that the
        membership-independent property the fix bought is the AUTHORIZATION verdict -- a caller
        authorized for no userRole row is refused identically in all four states.
        """
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=existing, deny=_deny_everything, known_roles=known_roles
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, f"{label}: {response}"
        batch.put_item.assert_not_called()
        batch.delete_item.assert_not_called()

    def test_a_known_role_still_updates(self):
        """Positive control: validating the added roles must not block an ordinary update."""
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=(_OLD_ROLE,), known_roles=(_OLD_ROLE, _NEW_ROLE)
        )
        try:
            response = userRolesService.handle_put_request(
                _event("PUT", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert batch.put_item.call_args.kwargs["Item"]["roleName"] == _NEW_ROLE

    def test_create_still_requires_every_named_role_to_exist(self):
        """POST only adds, so nothing there is exempt -- the relaxation is PUT-specific."""
        spy, user_roles_table, batch, audit, undo = _wire(existing=(), known_roles=())
        try:
            response = userRolesService.handle_post_request(
                _event("POST", {"userId": _USER, "roleName": [_NEW_ROLE]})
            )
        finally:
            undo()

        assert response["statusCode"] == 400, (
            f"POST created an assignment to a role that does not exist: {response}"
        )
        batch.put_item.assert_not_called()


@pytest.mark.unit
class TestDeleteAllIsAuthorizedAndTruthful:
    """A delete-all against a user holding nothing evaluated nothing and claimed a deletion.

    The loop iterates the assignments that exist, so an empty set skipped every enforce, fell
    through to the batch write and answered "User roles deleted successfully" -- a 200 for a
    caller authorized for no userRole row, plus an audit record for a deletion that never
    happened.
    """

    def test_an_empty_target_still_authorizes_the_operation(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=())
        try:
            response = userRolesService.handle_delete_request(
                _event("DELETE", {"userId": _USER})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert ("DELETE", None) in _authorized_pairs(spy), (
            f"a delete-all against a user with no assignments was answered without "
            f"authorizing anything; evaluated: {sorted(_authorized_pairs(spy), key=str)}"
        )

    @pytest.mark.parametrize(
        "label,existing",
        [("target holds a role", (_OLD_ROLE,)), ("target holds nothing", ())],
    )
    def test_a_denied_caller_is_refused_either_way(self, label, existing):
        spy, user_roles_table, batch, audit, undo = _wire(
            existing=existing, deny=_deny_everything
        )
        try:
            response = userRolesService.handle_delete_request(
                _event("DELETE", {"userId": _USER})
            )
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{label}: a caller authorized for no userRole row could read membership off the "
            f"delete-all status code: {response}"
        )
        batch.delete_item.assert_not_called()

    def test_a_no_op_delete_does_not_claim_a_deletion(self):
        spy, user_roles_table, batch, audit, undo = _wire(existing=())
        try:
            response = userRolesService.handle_delete_request(
                _event("DELETE", {"userId": _USER})
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        batch.delete_item.assert_not_called()
        assert (
            json.loads(response["body"])["message"]
            == userRolesService.USER_ROLE_DELETE_NO_CHANGES_MESSAGE
        ), f"a delete-all that removed nothing reported a deletion: {response}"

    def test_the_audit_record_says_whether_anything_was_deleted(self):
        for label, existing, expected_changed in (
            ("no-op", (), False),
            ("real deletion", (_OLD_ROLE,), True),
        ):
            spy, user_roles_table, batch, audit, undo = _wire(existing=existing)
            try:
                response = userRolesService.handle_delete_request(
                    _event("DELETE", {"userId": _USER})
                )
            finally:
                undo()

            assert response["statusCode"] == 200, f"{label}: {response}"
            assert audit.call_args_list, f"{label}: no audit record was written at all"
            recorded_changes = [call.args[2].get("changed") for call in audit.call_args_list]
            assert set(recorded_changes) == {expected_changed}, (
                f"{label}: the audit records say changed={recorded_changes}"
            )


@pytest.mark.unit
class TestANoOpIsAudited:
    """The decision: a write that changed nothing is still recorded, on BOTH endpoints.

    The alternative -- suppress the record -- was rejected. The subject of an auth-change record
    is the authorized attempt: who asked to rewrite whose role set, and when. Suppressing it
    would make a delete-all against an empty role set, or a PUT that names exactly the roles the
    user already holds, the one user-role write that leaves no trail, and those are precisely the
    shapes a membership probe takes. `changed` is the honest signal for whether anything was
    written, and `test_the_audit_record_says_whether_anything_changed` /
    `test_the_audit_record_says_whether_anything_was_deleted` pin its value.

    Pinned for both endpoints together so the two paths cannot drift: whoever decides to drop the
    no-op record has to drop it from both, and will see this test rather than a silent asymmetry.
    """

    @pytest.mark.parametrize(
        "label,method,handler_name,existing,body,event_type",
        [
            ("update", "PUT", "handle_put_request", (_OLD_ROLE,),
             {"userId": _USER, "roleName": [_OLD_ROLE]}, "userRoleUpdate"),
            ("delete", "DELETE", "handle_delete_request", (),
             {"userId": _USER}, "userRoleDelete"),
        ],
    )
    def test_a_no_op_still_writes_an_audit_record(
        self, label, method, handler_name, existing, body, event_type
    ):
        spy, user_roles_table, batch, audit, undo = _wire(existing=existing)
        try:
            response = getattr(userRolesService, handler_name)(_event(method, body))
        finally:
            undo()

        assert response["statusCode"] == 200, f"{label}: {response}"
        recorded = [(call.args[1], call.args[2].get("changed")) for call in audit.call_args_list]
        assert recorded, (
            f"{label}: a no-op write left no audit trail at all; the attempt is the record's "
            f"subject and `changed` is what says nothing was written"
        )
        assert (event_type, False) in recorded, (
            f"{label}: no {event_type} record marked changed=False; recorded: {recorded}"
        )


@pytest.mark.unit
class TestTier1StillDeniesEmptyTokens:
    """The upstream control that masks the Tier-2 defect in production stays in place."""

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_lambda_handler_denies_an_empty_token_list(self, method):
        spy = _EnforcerSpy()
        with patch.object(userRolesService, "CasbinEnforcer", spy.factory), patch.object(
            userRolesService, "request_to_claims", MagicMock(return_value={"tokens": []})
        ):
            response = userRolesService.lambda_handler(
                _event(method, {"userId": _USER, "roleName": [_NEW_ROLE]}), MagicMock()
            )

        assert response["statusCode"] == 403
        assert spy.constructions == [], (
            f"Tier 1 constructed an enforcer for an empty token list: {spy.constructions}"
        )
