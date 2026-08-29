# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tag mutations on the asset PUT path are authorized against the post-mutation asset.

A tag can carry object-level authority (a role scoped `tags contains X`), so evaluating
`enforce(asset, "PUT")` only against the asset as it is stored makes tag-based access
control self-service: the caller is authorized by the very tags the request is about to
replace. `update_asset` therefore evaluates the asset as it will be stored as well,
whenever the tag set actually changes.

The action matters as much as the state. The Casbin matcher compares the action for
equality (`r.act == p.act` in `PERMISSION_CONSTRAINT_POLICY`), and the four constraint
permissions are independent, so a rule that denies GET is invisible to any number of PUT
checks. A tag that gates *visibility* is the natural GET rule — which is why a tag change
is evaluated for GET as well: on the stored tag list, so a caller cannot drop a tag that
kept the asset out of their reach, and on the list to be stored, so they cannot attach
one. Evaluating only the state to be stored cannot catch the removal at all, because the
state to be stored is precisely the state the caller is permitted to see.

The pairing below is deliberate. The denial tests show the bypass is closed; the
unchanged-tags, unrelated-tag-change and rename tests are the controls that catch an
over-tightened check, which here would break ordinary editing for every tag-scoped role.
The object-shape test is what proves the denials are real rather than incidental:
`CasbinEnforcer` scrubs an object down to the fields valid for its `object__type`, so a
post-state object that lost `tags` (or `databaseId`) would satisfy every denial assertion
for the wrong reason.

Every assertion about what was authorized is made against the SET of (action, state) pairs
handed to Casbin -- never a call count nor a position in the sequence. An implementation made
strictly safer, by evaluating an additional action or the same object twice, must not turn
these red. Where the property is that an evaluation must NOT happen, it is measured
behaviourally instead: the caller a widened check would lock out is the one whose edit is
asserted to succeed, against a constraint that refuses the evaluation in question. A count
would report the same fact while also failing every harmless implementation.
"""

import json
import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Reuse the real assetService loader and its env/table wiring.
from tests.handlers.assets.test_assetService_update_tag_scope import (  # noqa: E402
    _load_asset_service,
    _existing_asset,
)

_DB = "db-a"
_ASSET = "asset-1"
_SCOPING_TAG = "restricted"


@contextmanager
def _tag_existence_validation_stubbed():
    """Neutralize the lazily imported tag-scope validators.

    update_asset imports validate_tags_exist / verify_all_required_tags_satisfied from
    handlers.assets.createAsset at call time. Tag existence scoping is covered by
    test_assetService_update_tag_scope.py; these tests are about authorization, so the
    scope validators are stubbed to no-ops and cannot stand in for an authorization
    result.
    """
    saved = sys.modules.get("handlers.assets.createAsset")
    stub = types.ModuleType("handlers.assets.createAsset")
    stub.validate_tags_exist = MagicMock()
    stub.verify_all_required_tags_satisfied = MagicMock()
    sys.modules["handlers.assets.createAsset"] = stub
    try:
        yield stub
    finally:
        if saved is not None:
            sys.modules["handlers.assets.createAsset"] = saved
        else:
            sys.modules.pop("handlers.assets.createAsset", None)


def _recording_enforcer(predicate, record):
    """A CasbinEnforcer stand-in that applies `predicate` and records each enforced object."""
    class _Enforcer:
        def __init__(self, claims_and_roles):
            self.claims_and_roles = claims_and_roles

        def enforce(self, obj, action):
            record.append({"object": dict(obj), "action": action})
            return predicate(obj, action)

        def enforceAPI(self, event):
            return True

    return _Enforcer


def _allow_only_scoped_tag(obj, action):
    """An ALLOW rule scoped `tags contains 'restricted'` and nothing else."""
    return _SCOPING_TAG in (obj.get("tags") or [])


def _deny_secret_tag(obj, action):
    """A DENY rule keyed on `tags contains 'secret'`."""
    return "secret" not in (obj.get("tags") or [])


def _deny_get_on_scoping_tag(obj, action):
    """A visibility deny: the tag hides the asset instead of write-protecting it.

    An ALLOW on asset GET/PUT for the database, plus `{objectType: asset, criteriaAnd:
    [{field: tags, operator: is_one_of, value: 'restricted'}], groupPermissions:
    [{permission: GET, permissionType: deny}]}`. Each groupPermissions entry is validated
    on its own (models/roleConstraints.py), so a deny scoped to GET alone is authorable —
    the shipped deny-tagged-assets.json happens to deny PUT/POST/DELETE instead, which is
    what makes that one template self-protecting. Here writes stay allowed, so every PUT
    evaluation of every state passes and only a GET evaluation refuses.
    """
    if action == "GET" and _SCOPING_TAG in (obj.get("tags") or []):
        return False
    return True


def _allow_only_proj_asset_names(obj, action):
    """An ALLOW rule scoped `assetName starts_with 'PROJ-'`."""
    return str(obj.get("assetName") or "").startswith("PROJ-")


def _wire(m, existing_tags=(), asset_name="N1"):
    asset = _existing_asset(_DB)
    asset["tags"] = list(existing_tags)
    asset["assetName"] = asset_name
    m.get_asset_details = MagicMock(return_value=dict(asset))
    m.asset_table = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.send_subscription_email = MagicMock()
    return m


def _attempt_update(m, update_data, claims_and_roles=None):
    try:
        return m.update_asset(
            _DB, _ASSET, update_data, claims_and_roles or {"tokens": ["u1"]}
        )
    except Exception as exc:  # noqa: BLE001 - a denial may be raised or returned
        return exc


def _tag_evaluations(record):
    """The (action, tags) pairs handed to Casbin, as a set.

    A set, deliberately: the property is which state was evaluated for which action, not how
    many evaluations happened or in what order. Tag lists become tuples so they can be members.
    """
    return {
        (call["action"], tuple(call["object"].get("tags") or ())) for call in record
    }


def _name_evaluations(record):
    """The (action, assetName) pairs handed to Casbin, as a set."""
    return {(call["action"], call["object"].get("assetName")) for call in record}


def _refusals(record, predicate):
    """Every recorded evaluation the constraint refused.

    Derived by re-applying the constraint rather than by reading the last recorded call: an
    implementation that evaluated everything before deciding would still be refusing the same
    objects, and a position in the sequence would report it as a different defect.
    """
    return [call for call in record if not predicate(call["object"], call["action"])]


def _assert_denied(result):
    """Accept either denial shape: a raised error or an authorization_error() response."""
    if isinstance(result, Exception):
        return
    if isinstance(result, dict):
        assert result.get("statusCode") == 403, (
            f"update_asset returned a non-403 response for a denied edit: {result}"
        )
        return
    pytest.fail(f"update_asset returned a success response for a denied edit: {result}")


@pytest.mark.unit
class TestTagScopedCallerCannotEscapeTheirScope:
    """The bypass: mutating the tag that grants (or denies) the caller's own access."""

    def test_removing_the_tag_that_scopes_the_caller_is_denied(self):
        """A caller allowed only by `tags contains 'restricted'` cannot drop that tag.

        The pre-mutation asset carries the tag, so the stored-state check alone permits
        the write — this is the escape the post-mutation check closes.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_only_scoped_tag, record)):
            result = _attempt_update(m, {"tags": []})

        m.asset_table.put_item.assert_not_called()
        _assert_denied(result)
        # This constraint permits every evaluation of the stored state (it still carries the
        # tag), so the denial can only have come from evaluating the asset as it would be
        # stored -- and every refusal recorded is one of those.
        evaluated = _tag_evaluations(record)
        # The stored tag list is evaluated -- for the write, for visibility, or both. The ACTION
        # is deliberately not named here: which of the stored-state evaluations survives depends
        # on where the tag gate sits relative to the stored-state authorize call, and a gate
        # placed first refuses before the stored-state PUT is reached while returning the
        # identical verdict for every input. That the stored state is authorized for the write is
        # pinned in test_ordinary_edit_by_the_same_scoped_caller_still_succeeds, where no denial
        # can short-circuit it. See TestTheEvaluationAssertionsAreOrderIndependent.
        assert (_SCOPING_TAG,) in {tags for _, tags in evaluated}, (
            f"the stored tag list was never evaluated at all: {sorted(evaluated)}"
        )
        assert () in {tags for _, tags in evaluated}, (
            f"the post-mutation (empty) tag list was never enforced; enforced: "
            f"{sorted(evaluated)}"
        )
        refused = _refusals(record, _allow_only_scoped_tag)
        assert refused, "nothing was refused, so the denial came from elsewhere"
        for call in refused:
            assert tuple(call["object"].get("tags") or ()) == (), (
                f"the refusal was decided on tag list {call['object'].get('tags')} rather "
                f"than the list to be stored"
            )

    def test_removing_a_tag_whose_deny_is_scoped_to_get_is_denied(self):
        """The residual escape: the tag deny names GET, the mutation is a PUT.

        The caller holds asset PUT and is denied GET on `tags is_one_of 'restricted'`, so
        they cannot see the asset. Dropping the tag makes it visible to them. Every PUT
        evaluation — stored state and state to be stored alike — is allowed by this
        constraint, so the refusal can only come from evaluating the visibility action.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_scoping_tag, record)):
            result = _attempt_update(m, {"tags": []})

        m.asset_table.put_item.assert_not_called()
        _assert_denied(result)
        evaluated = _tag_evaluations(record)
        assert ("GET", (_SCOPING_TAG,)) in evaluated, (
            f"the stored tag list was never evaluated for the visibility action, and this "
            f"constraint allows every PUT; a GET-scoped deny is only honoured by evaluating "
            f"GET. Evaluated: {sorted(evaluated)}"
        )
        refused = _refusals(record, _deny_get_on_scoping_tag)
        assert refused, "nothing was refused, so the denial came from elsewhere"
        for call in refused:
            assert call["action"] == "GET"
            assert tuple(call["object"].get("tags") or ()) == (_SCOPING_TAG,), (
                f"the refusal was decided on tag list {call['object'].get('tags')}; the tag "
                f"that hid the asset is on the STORED list, so the stored state is the one "
                f"that has to be evaluated for GET — the list to be stored is exactly the "
                f"state the caller is allowed to see"
            )

    def test_a_put_only_check_would_have_permitted_that_removal(self):
        """Control: the constraint above allows both PUT evaluations.

        Proves the preceding test measures the added action rather than a fixture that
        refuses everything: an implementation that evaluated only "PUT" — on the stored
        state, on the state to be stored, or on both — would have written the removal.
        """
        stored = {"object__type": "asset", "databaseId": _DB, "tags": [_SCOPING_TAG]}
        to_be_stored = {"object__type": "asset", "databaseId": _DB, "tags": []}

        assert _deny_get_on_scoping_tag(stored, "PUT") is True
        assert _deny_get_on_scoping_tag(to_be_stored, "PUT") is True
        # Only the visibility action refuses, and only on the stored tag list.
        assert _deny_get_on_scoping_tag(stored, "GET") is False
        assert _deny_get_on_scoping_tag(to_be_stored, "GET") is True

    def test_attaching_a_tag_that_hides_the_asset_from_the_caller_is_denied(self):
        """The mirror of the removal: tagging an asset out of the caller's own reach.

        Pinned as a decision rather than a side effect. The state to be stored is
        evaluated for GET too, so a tag edit cannot conceal an asset from the caller —
        which is how that tag conceals it from every role sharing the deny.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_scoping_tag, record)):
            result = _attempt_update(m, {"tags": [_SCOPING_TAG]})

        m.asset_table.put_item.assert_not_called()
        _assert_denied(result)
        evaluated = _tag_evaluations(record)
        assert ("GET", (_SCOPING_TAG,)) in evaluated, (
            f"the tag list to be stored was never evaluated for visibility: "
            f"{sorted(evaluated)}"
        )
        refused = _refusals(record, _deny_get_on_scoping_tag)
        assert refused, "nothing was refused, so the denial came from elsewhere"
        for call in refused:
            assert call["action"] == "GET"
            assert tuple(call["object"].get("tags") or ()) == (_SCOPING_TAG,), (
                f"the refusal was decided on tag list {call['object'].get('tags')} rather "
                f"than the list to be stored"
            )

    def test_an_unrelated_tag_change_by_the_same_caller_still_succeeds(self):
        """Control: the GET evaluations must not gate tag edits the deny does not touch.

        Same GET-scoped deny, an asset that does not carry the named tag, and a tag added
        that the deny does not name. If this fails, the widened action set gates every tag
        edit instead of only the ones that move the asset across a scope boundary.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=["keepme"])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_scoping_tag, record)):
            result = _attempt_update(m, {"tags": ["keepme", "draft"]})

        assert getattr(result, "success", None) is True, (
            f"a tag edit unrelated to the deny was refused: {result}"
        )
        m.asset_table.put_item.assert_called_once()
        assert m.asset_table.put_item.call_args.kwargs["Item"]["tags"] == ["keepme", "draft"]
        # Both states were still evaluated for both actions; the deny simply does not name
        # either tag list. Asserted as containment so a safer implementation stays green.
        assert {
            ("PUT", ("keepme",)),
            ("GET", ("keepme",)),
            ("GET", ("keepme", "draft")),
            ("PUT", ("keepme", "draft")),
        } <= _tag_evaluations(record), (
            f"an evaluation the tag gate owes was skipped: {sorted(_tag_evaluations(record))}"
        )

    def test_an_ordinary_edit_by_the_get_denied_caller_still_succeeds(self):
        """Control: no tag change, no added gate — for the caller the deny hides it from.

        The stored asset carries the tag this caller is denied GET on, so a GET evaluation
        placed outside the tag-change guard would make the asset permanently uneditable.
        Editing the description is not a tag change and stays gated exactly as before.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_scoping_tag, record)):
            result = _attempt_update(m, {"description": "an ordinary edit"})

        # The success IS the property: this caller is denied GET on the stored tag list, so a
        # visibility evaluation placed outside the tag-change guard would refuse this edit.
        # Measured behaviourally rather than by counting evaluations, which would also fail an
        # implementation that authorized the same state more than once.
        assert getattr(result, "success", None) is True, (
            f"an edit that does not touch tags was refused: {result}; the tag-change "
            f"evaluations must not fire when the tag set is unchanged"
        )
        m.asset_table.put_item.assert_called_once()
        assert not _refusals(record, _deny_get_on_scoping_tag), (
            f"an evaluation of this asset was refused even though the edit does not touch "
            f"tags: {_refusals(record, _deny_get_on_scoping_tag)}"
        )

    def test_adding_a_tag_outside_the_callers_scope_is_denied(self):
        """A tag the caller is denied cannot be attached to an asset they can edit."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_secret_tag, record)):
            result = _attempt_update(m, {"tags": ["secret"]})

        m.asset_table.put_item.assert_not_called()
        _assert_denied(result)

    def test_ordinary_edit_by_the_same_scoped_caller_still_succeeds(self):
        """Control: the caller of the first test keeps ordinary edit rights.

        Same role, same asset, same scoping tag — only the tag set is left alone. If this
        fails, the post-mutation check is gating edits it has no business gating and
        normal editing is broken for every tag-scoped role.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_only_scoped_tag, record)):
            result = _attempt_update(m, {"description": "an ordinary edit"})

        assert getattr(result, "success", None) is True, (
            f"an edit that does not touch tags was refused: {result}"
        )
        m.asset_table.put_item.assert_called_once()
        assert m.asset_table.put_item.call_args.kwargs["Item"]["description"] == (
            "an ordinary edit"
        )
        # The stored state was evaluated for the write, as it always is. That the tag-change
        # evaluations do not fire for a non-tag edit is measured where it has a consequence --
        # test_an_ordinary_edit_by_the_get_denied_caller_still_succeeds, whose caller a widened
        # check would lock out. Counting evaluations here would report the same fact while also
        # failing an implementation that authorized this state twice.
        assert ("PUT", (_SCOPING_TAG,)) in _tag_evaluations(record), (
            f"the stored state was never evaluated for the write: "
            f"{sorted(_tag_evaluations(record))}"
        )

    def test_resubmitting_the_identical_tag_list_still_succeeds(self):
        """Control: a full-object PUT re-sends the tag list unchanged."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_only_scoped_tag, record)):
            result = _attempt_update(m, {"tags": [_SCOPING_TAG], "description": "same tags"})

        assert getattr(result, "success", None) is True, (
            f"resubmitting the identical tag list was refused: {result}"
        )
        assert m.asset_table.put_item.call_args.kwargs["Item"]["tags"] == [_SCOPING_TAG]

    def test_the_get_denied_caller_can_resubmit_the_identical_tag_list(self):
        """The same control where it bites: a resubmission must not count as a tag change.

        This caller is denied GET on the stored tag list, so treating an identical resubmission
        as a change would evaluate that list for visibility and refuse -- which is what every
        full-object PUT from the UI sends. Behavioural, so an implementation that authorizes the
        same state more than once is unaffected.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[_SCOPING_TAG])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_scoping_tag, record)):
            result = _attempt_update(m, {"tags": [_SCOPING_TAG], "description": "same tags"})

        assert getattr(result, "success", None) is True, (
            f"a full-object PUT resubmitting the stored tag list was refused: {result}"
        )
        assert m.asset_table.put_item.call_args.kwargs["Item"]["tags"] == [_SCOPING_TAG]


@pytest.mark.unit
class TestTheEvaluationAssertionsAreOrderIndependent:
    """The assertions above must not encode the ORDER of two blocks with one verdict.

    ``update_asset`` authorizes the stored state for PUT and then, when the tag set changes,
    evaluates ``(stored, GET)``, ``(post, GET)``, ``(post, PUT)`` and raises on the first
    refusal. Moving the tag-change block ahead of the stored-state authorize call leaves the
    verdict identical for every input -- a refusal is a refusal whichever check reaches it
    first -- but it removes the stored-state PUT evaluation from the record when the tag gate
    refuses. An assertion naming ``("PUT", stored_tags)`` therefore fails a reordering that is
    not a defect, which trains the next author toward the one ordering the test happened to see.

    Measured on synthetic records rather than by reordering the handler: the two orderings can
    be written down exactly, so the property is checked without a source edit -- and the check
    lives in the suite instead of in a one-off verification run.
    """

    #: What the record looks like when the stored-state authorize runs first (today).
    _GATE_LAST = [
        {"action": "PUT", "object": {"tags": [_SCOPING_TAG]}},
        {"action": "GET", "object": {"tags": [_SCOPING_TAG]}},
        {"action": "GET", "object": {"tags": []}},
    ]
    #: ... and when the tag gate runs first and refuses before the stored-state PUT is reached.
    _GATE_FIRST = [
        {"action": "GET", "object": {"tags": [_SCOPING_TAG]}},
        {"action": "GET", "object": {"tags": []}},
    ]
    #: The implementation the assertion must still catch: the stored state never looked at.
    _STORED_STATE_NEVER_EVALUATED = [
        {"action": "GET", "object": {"tags": []}},
        {"action": "PUT", "object": {"tags": []}},
    ]

    @staticmethod
    def _stored_list_was_evaluated(record):
        """The assertion the removal test now makes, applied to a record."""
        return (_SCOPING_TAG,) in {tags for _, tags in _tag_evaluations(record)}

    @staticmethod
    def _stored_list_was_evaluated_for_put(record):
        """The order-sensitive form this replaced, kept only to demonstrate the difference."""
        return ("PUT", (_SCOPING_TAG,)) in _tag_evaluations(record)

    def test_the_assertion_holds_under_both_orderings(self):
        assert self._stored_list_was_evaluated(self._GATE_LAST)
        assert self._stored_list_was_evaluated(self._GATE_FIRST)

    def test_the_replaced_form_did_not(self):
        """The defect, stated: identical verdict, one ordering red.

        If this ever starts passing for _GATE_FIRST the note above is stale and the stricter
        assertion can come back.
        """
        assert self._stored_list_was_evaluated_for_put(self._GATE_LAST)
        assert not self._stored_list_was_evaluated_for_put(self._GATE_FIRST)

    def test_the_assertion_still_catches_a_missing_stored_state_check(self):
        """Positive control: the looser form is not satisfied by everything."""
        assert not self._stored_list_was_evaluated(self._STORED_STATE_NEVER_EVALUATED)


@pytest.mark.unit
class TestPostMutationObjectCarriesTheTags:
    """Proves the denials above are real: the enforced object keeps the fields that scope it."""

    def test_the_enforced_post_state_carries_the_new_tags(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=["keepme"], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(lambda o, a: True, record)):
            result = _attempt_update(m, {"tags": ["keepme", "added"]})

        assert getattr(result, "success", None) is True
        # The whole decision, pinned as the set of evaluations the gate owes: the stored tag
        # list is evaluated for write and for visibility, the list to be stored for both.
        assert {
            ("PUT", ("keepme",)),
            ("GET", ("keepme",)),
            ("GET", ("keepme", "added")),
            ("PUT", ("keepme", "added")),
        } <= _tag_evaluations(record), (
            f"an evaluation the tag gate owes was skipped: {sorted(_tag_evaluations(record))}"
        )
        post_states = [
            call["object"] for call in record
            if call["object"].get("tags") == ["keepme", "added"]
        ]
        assert post_states, (
            f"no object handed to enforce carried the requested tags: "
            f"{[call['object'].get('tags') for call in record]}"
        )

    def test_the_enforced_post_state_is_a_full_asset_not_a_partial(self):
        """The other scoped fields must survive, or every database-scoped ALLOW stops matching."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(lambda o, a: True, record)):
            _attempt_update(m, {"tags": ["added"]})

        assert record, "no enforce call was made at all"
        for call in record:
            assert call["object"].get("databaseId") == _DB, (
                f"enforce received an object with no databaseId: {call['object']}"
            )
            assert call["object"].get("assetName") == "PROJ-1", (
                f"enforce received an object with no assetName: {call['object']}"
            )
            assert call["object"].get("object__type") == "asset"

    def test_tags_survive_object_field_scrubbing_for_an_asset(self):
        """Without `tags` among the asset's constraint fields the post-state check is inert.

        `CasbinEnforcer._scrub_object_fields` keeps only the keys returned by
        `get_constraint_fields_for_object_type(object__type)` (plus the control keys), so a
        tag-scoped rule can only fire if `tags` is one of them. Asserted on that field
        matrix, which is the input the scrub reads; the scrub itself is covered against the
        real enforcer in tests/handlers/authz/test_constraint_field_scrub.py.
        """
        from common.constants import get_constraint_fields_for_object_type

        asset_fields = get_constraint_fields_for_object_type("asset")
        assert "tags" in asset_fields, (
            f"`tags` is not a constraint field for object type asset ({asset_fields}); the "
            f"post-mutation check would be scrubbed away"
        )
        assert "databaseId" in asset_fields

    def test_the_matcher_compares_the_action_for_equality(self):
        """Why the tag-change gate names GET explicitly rather than relying on PUT.

        The shipped Casbin matcher tests `r.act == p.act`, and the four constraint
        permissions are independent values, so a constraint whose groupPermissions entry is
        GET is never consulted by an `enforce(obj, "PUT")` — however many PUT evaluations
        are added, and whichever state they run against. If this ever becomes a wildcard or
        prefix match, the enumerated actions in `update_asset` can be revisited.
        """
        from common.constants import (
            ALLOWED_CONSTRAINT_PERMISSIONS,
            PERMISSION_CONSTRAINT_POLICY,
        )

        assert "r.act == p.act" in PERMISSION_CONSTRAINT_POLICY, (
            f"the action matcher is no longer an equality test: "
            f"{PERMISSION_CONSTRAINT_POLICY}"
        )
        assert "GET" in ALLOWED_CONSTRAINT_PERMISSIONS
        assert "PUT" in ALLOWED_CONSTRAINT_PERMISSIONS


@pytest.mark.unit
class TestAdjacentBehaviourIsPinned:
    """The decisions the post-mutation check makes about everything that is not a tag."""

    def test_a_rename_on_its_own_is_not_re_enforced(self):
        """A rename with no tag change is evaluated against the stored name only.

        With `assetName starts_with 'PROJ-'` as the only ALLOW, renaming PROJ-1 to OTHER
        succeeds. The post-mutation check is scoped to tag changes, so an unrelated field
        edit is gated exactly as it was.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_only_proj_asset_names, record)):
            result = _attempt_update(m, {"assetName": "OTHER"})

        # The success IS the property: this constraint refuses the new name, so any evaluation
        # of the post-rename record would refuse the write.
        assert getattr(result, "success", None) is True, (
            f"a rename out of an assetName-scoped ALLOW is now refused: {result}"
        )
        assert m.asset_table.put_item.call_args.kwargs["Item"]["assetName"] == "OTHER"
        assert ("PUT", "PROJ-1") in _name_evaluations(record), (
            f"the stored record was never evaluated for the write: "
            f"{sorted(_name_evaluations(record), key=str)}"
        )

    def test_a_rename_combined_with_a_tag_change_is_evaluated_on_the_new_name(self):
        """A tag change makes the record-to-be-stored the subject of the check.

        The non-tag edits are applied before the tag gate, so the two objects it evaluates
        both carry the new name and differ only in their tag lists. A request that renames
        AND retags is therefore evaluated against the new name — pinned so the wider reach
        of the tag gate is a stated behaviour rather than a side effect.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_only_proj_asset_names, record)):
            result = _attempt_update(m, {"assetName": "OTHER", "tags": ["added"]})

        m.asset_table.put_item.assert_not_called()
        _assert_denied(result)
        # The stored-state check runs before any field is edited and sees the stored name;
        # every tag-change evaluation sees the new one. Asserted as membership of the evaluated
        # (action, assetName) set rather than by position, so an implementation that evaluates
        # more than the minimum stays green.
        evaluated = _name_evaluations(record)
        assert ("PUT", "PROJ-1") in evaluated, (
            f"the stored record was never evaluated: {sorted(evaluated, key=str)}"
        )
        refused = _refusals(record, _allow_only_proj_asset_names)
        assert refused, "nothing was refused, so the denial came from elsewhere"
        for call in refused:
            assert call["object"].get("assetName") == "OTHER", (
                f"the refusal was decided on name {call['object'].get('assetName')} rather "
                f"than the name to be stored"
            )

    def test_a_system_user_cross_call_still_writes(self):
        """A lambdaCrossCall arrives as tokens=['SYSTEM_USER'] and must keep writing."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(lambda o, a: True, record)):
            result = _attempt_update(m, {"tags": ["added"]}, {"tokens": ["SYSTEM_USER"]})

        assert getattr(result, "success", None) is True
        m.asset_table.put_item.assert_called_once()


@pytest.mark.unit
class TestDenialReachesTheCaller:
    """The PUT request handler must return the denial, not convert it into a 500."""

    def test_a_denied_tag_addition_returns_403_from_the_put_handler(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])
        event = {
            "requestContext": {
                "http": {"method": "PUT", "path": f"/database/{_DB}/assets/{_ASSET}"}
            },
            "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
            "queryStringParameters": None,
            "body": json.dumps({"tags": ["secret"]}),
        }
        saved_claims = getattr(m, "claims_and_roles", {})
        m.claims_and_roles = {"tokens": ["u1"]}
        try:
            with _tag_existence_validation_stubbed(), \
                    patch.object(m, "CasbinEnforcer",
                                 _recording_enforcer(_deny_secret_tag, record)):
                response = m.handle_put_request(event)
        finally:
            m.claims_and_roles = saved_claims

        assert response["statusCode"] == 403, (
            f"a denied tag addition surfaced as {response['statusCode']} instead of 403: "
            f"{response}"
        )
        m.asset_table.put_item.assert_not_called()
