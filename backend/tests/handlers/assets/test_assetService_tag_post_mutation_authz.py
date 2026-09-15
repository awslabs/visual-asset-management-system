# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-047: a tag edit must be authorized against the POST-mutation asset too.

``update_asset`` runs its single ``enforce(asset, "PUT")`` against the asset as it
is stored, then mutates ``tags``. Tag-scoped access control is therefore
self-service: a caller holding asset PUT can add a tag whose scope they are denied
(or remove one that a deny rule keys on) and move the asset out of the deny scope
in one request, because the rule is evaluated against the pre-mutation tag list.

Per the owner's decision this is closed by option (b) only -- re-evaluate the
asset-level enforce against the post-mutation asset -- with no per-tag `tag`
objectType authority check. The controls below pin the three ways that second
enforce can silently do nothing or do too much: a partial post-state object whose
missing ``databaseId``/``assetName`` breaks every database-scoped ALLOW, an
ungated post-check that newly gates unrelated edits (including renames), and a
post-check placed outside the ``len(tokens) > 0`` guard.
"""

import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Reuse the real assetService loader and its env/table wiring.
from tests.handlers.assets.test_assetService_update_tag_scope import (  # noqa: E402
    _load_asset_service,
    _existing_asset,
    _written_attributes,
)

_DB = "db-a"
_ASSET = "asset-1"


@contextmanager
def _tag_scope_validation_stubbed():
    """Neutralize the lazily imported tag-scope validators.

    update_asset imports validate_tags_exist / verify_all_required_tags_satisfied
    from handlers.assets.createAsset at call time. Tag *existence* scoping is
    covered by test_assetService_update_tag_scope.py; these tests are about
    authorization, so the scope validators are stubbed to no-ops and cannot mask
    an authorization result behind a tag-not-found rejection.
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


def _enforcer(predicate, record):
    """A CasbinEnforcer stand-in that applies `predicate` and records every object.

    Recording is what makes the post-state assertions possible: the test can check
    the exact object shape the enforce call received, not merely its verdict.
    """
    class _Enforcer:
        def __init__(self, claims_and_roles):
            self.claims_and_roles = claims_and_roles

        def enforce(self, obj, action):
            record.append({"object": dict(obj), "action": action})
            return predicate(obj, action)

        def enforceAPI(self, event):
            return True

    return _Enforcer


def _tag_evaluations(record):
    """The (action, tags) pairs handed to Casbin, as a set.

    A set, deliberately: the property is which state was evaluated for which action, not how
    many evaluations happened or in what order. An implementation made strictly safer, by
    evaluating an additional action or the same object twice, must not turn these red.
    """
    return {
        (call["action"], tuple(call["object"].get("tags") or ())) for call in record
    }


def _refusals(record, predicate):
    """Every recorded evaluation the constraint refused, by re-applying the constraint."""
    return [call for call in record if not predicate(call["object"], call["action"])]


def _deny_restricted_tag(obj, action):
    """A `tags contains 'restricted'` deny rule."""
    return "restricted" not in (obj.get("tags") or [])


def _deny_get_on_restricted_tag(obj, action):
    """A visibility deny: `tags contains 'restricted'` refuses GET and permits every write.

    Used where the property is that an evaluation must NOT happen. A caller denied GET on the
    stored tag list is the one a check placed outside the tag-change guard would lock out, so
    their edit succeeding is the behavioural form of "the post-check is gated on tags" -- and
    unlike a call count it stays green for an implementation that authorizes the same state
    twice.
    """
    if action == "GET" and "restricted" in (obj.get("tags") or []):
        return False
    return True


def _allow_only_database_a(obj, action):
    """A `databaseId equals db-a` ALLOW rule and nothing else.

    A partial post-state object (just {'tags': ..., 'object__type': 'asset'}) is
    scrubbed to placeholder values for the missing fields, so this rule stops
    matching and every tag edit 403s.
    """
    return obj.get("databaseId") == _DB


def _allow_only_proj_asset_names(obj, action):
    """An `assetName starts_with 'PROJ-'` ALLOW rule."""
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


def _attempt_update(m, update_data, claims_and_roles):
    try:
        return m.update_asset(_DB, _ASSET, update_data, claims_and_roles)
    except Exception as exc:  # noqa: BLE001 - the deny may be raised or returned
        return exc


def _assert_denied(result):
    """Accept either shape of denial: a raised error or an authorization_error() dict."""
    if isinstance(result, Exception):
        return
    if isinstance(result, dict):
        assert result.get("statusCode") == 403, (
            f"update_asset returned a non-403 response for a denied edit: {result}"
        )
        return
    pytest.fail(f"update_asset returned a success response for a denied edit: {result}")


@pytest.mark.unit
class TestTagAdditionIsAuthorizedAgainstPostState:
    """FIX-047 -- the bypass itself."""

    def test_adding_a_denied_tag_is_refused_and_not_written(self):
        """FIX-047: with a `tags contains 'restricted'` deny, adding it must be refused.

        Asserted on the write, not only the status code: the mutation must never
        reach put_item.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(_deny_restricted_tag, record)):
            result = _attempt_update(m, {"tags": ["restricted"]}, {"tokens": ["u1"]})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)

    def test_tag_removal_is_refused_on_the_pre_state_it_already_violates(self):
        """A removal whose stored state is already denied never reaches the post-state check.

        The pre-state here carries 'restricted', so the stored-state enforce refuses the
        write before the requested tag list is evaluated -- the removal cannot be used to
        talk its way past a rule the asset already violates. The post-state evaluation of a
        REMOVAL is exercised where the stored state permits it (a role scoped
        `tags contains ...` dropping its own scoping tag), in
        test_assetService_tag_mutation_authz.py.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=["restricted"])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(_deny_restricted_tag, record)):
            result = _attempt_update(m, {"tags": []}, {"tokens": ["u1"]})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)
        evaluated_tag_sets = {tags for _, tags in _tag_evaluations(record)}
        assert evaluated_tag_sets == {("restricted",)}, (
            f"expected the stored tag list to be the only state evaluated before the refusal; "
            f"enforced tag sets were {sorted(evaluated_tag_sets)}"
        )

    def test_deny_rule_is_live_on_the_pre_state(self):
        """Control: proves the fake enforcer actually denies.

        Without this, the two tests above could be satisfied by an enforcer that
        never allows anything, or by put_item never being reachable.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=["restricted"])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(_deny_restricted_tag, record)):
            result = _attempt_update(m, {"description": "edited"}, {"tokens": ["u1"]})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)
        # The stored tag list was evaluated and refused. Asserted on the state rather than on a
        # particular (action, state) pair: an implementation that requires an additional action
        # first refuses at that check instead, which is strictly safer and not this test's
        # subject.
        assert ("restricted",) in {tags for _, tags in _tag_evaluations(record)}, (
            f"the stored state was never evaluated: {sorted(_tag_evaluations(record))}"
        )
        assert _refusals(record, _deny_restricted_tag), (
            "nothing was refused, so this fixture does not deny and the tests above hold for "
            "the wrong reason"
        )


@pytest.mark.unit
class TestPostStateObjectShape:
    """FIX-047 control: the post-state object must be the FULL asset dict.

    CONSTRAINT_OBJECT_TYPE_FIELDS['asset'] keeps databaseId, assetName, assetType
    and tags. A post-state object built as a partial ({'tags': ..., 'object__type':
    'asset'}) leaves the other fields at placeholder values, so any
    databaseId-scoped ALLOW stops matching and EVERY tag edit 403s.
    """

    def test_database_scoped_allow_still_permits_a_tag_addition(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(_allow_only_database_a, record)):
            result = _attempt_update(m, {"tags": ["ok"]}, {"tokens": ["u1"]})

        assert getattr(result, "success", None) is True, (
            f"a databaseId-scoped ALLOW no longer permits a tag addition: {result}"
        )
        m.asset_table.update_item.assert_called_once()
        assert _written_attributes(m.asset_table)["tags"] == ["ok"]

    def test_every_enforced_object_carries_the_scoped_fields(self):
        """Control: each enforce call must see databaseId and assetName populated.

        Pins the object shape rather than only the verdict, so a partial post-state
        object fails here even where a permissive rule would hide it.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(lambda o, a: True, record)):
            _attempt_update(m, {"tags": ["ok"]}, {"tokens": ["u1"]})

        assert record, "no enforce call was made at all"
        for call in record:
            assert call["object"].get("databaseId") == _DB, (
                f"enforce received an object with no databaseId: {call['object']}"
            )
            assert call["object"].get("assetName"), (
                f"enforce received an object with no assetName: {call['object']}"
            )
            assert call["object"].get("object__type") == "asset"


@pytest.mark.unit
class TestUnrelatedEditsStayUngated:
    """FIX-047 controls: the post-check must be scoped to tag changes.

    The regression surface calls for gating the second enforce on
    ``'tags' in update_data``. These tests pin that decision so the wider
    behaviour change (re-gating renames and description edits) is deliberate
    rather than incidental.
    """

    def test_description_only_put_is_not_gated_by_the_tag_change_checks(self):
        """A PUT with no `tags` key must not pick up the tag-change evaluations.

        Measured against the caller a widened check would lock out: this constraint refuses a
        GET evaluation of the stored tag list, so a check placed outside the `'tags' in
        update_data` guard would make the asset permanently uneditable. The success is the
        property -- a count of enforce calls would report the same fact while also failing an
        implementation that authorized the stored state twice.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=["restricted"])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _enforcer(_deny_get_on_restricted_tag, record)):
            result = _attempt_update(m, {"description": "edited"}, {"tokens": ["u1"]})

        assert getattr(result, "success", None) is True, (
            f"a PUT with no `tags` key was refused: {result}; the post-mutation check must be "
            f"gated on 'tags' in update_data"
        )
        m.asset_table.update_item.assert_called_once()
        assert not _refusals(record, _deny_get_on_restricted_tag), (
            f"an evaluation of this asset was refused even though the request carries no tag "
            f"change: {_refusals(record, _deny_get_on_restricted_tag)}"
        )

    def test_the_get_deny_used_above_is_live(self):
        """Positive control: the constraint really does refuse a GET on the stored tag list.

        Without this, the test above would pass against a fixture that permits everything, and
        its "not refused" assertion would prove nothing.
        """
        assert _deny_get_on_restricted_tag({"tags": ["restricted"]}, "GET") is False
        assert _deny_get_on_restricted_tag({"tags": ["restricted"]}, "PUT") is True
        assert _deny_get_on_restricted_tag({"tags": []}, "GET") is True

    def test_rename_only_put_is_not_re_enforced_post_mutation(self):
        """Explicit decision: a rename out of an assetName-scoped ALLOW is NOT re-checked.

        With `assetName starts_with 'PROJ-'` as the only ALLOW, renaming PROJ-1 to
        OTHER succeeds because only the pre-mutation name is evaluated. If the fix
        places the post-check after all mutations rather than gating it on tags,
        this test fails -- which is the signal that the behaviour change was not
        deliberate.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _enforcer(_allow_only_proj_asset_names, record)):
            result = _attempt_update(m, {"assetName": "OTHER"}, {"tokens": ["u1"]})

        assert getattr(result, "success", None) is True, (
            f"a rename out of an assetName-scoped ALLOW is now refused: {result}"
        )
        assert _written_attributes(m.asset_table)["assetName"] == "OTHER"

    def test_identical_tag_list_resubmission_still_succeeds(self):
        """Control: re-submitting the same tags (what every full-object PUT does)."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=["keepme"])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(lambda o, a: True, record)):
            result = _attempt_update(m, {"tags": ["keepme"]}, {"tokens": ["u1"]})

        assert getattr(result, "success", None) is True
        assert _written_attributes(m.asset_table)["tags"] == ["keepme"]

    def test_system_user_cross_call_still_writes(self):
        """Control: the post-check must stay inside the `len(tokens) > 0` guard.

        A lambdaCrossCall arrives as tokens=['SYSTEM_USER'] and must keep writing.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[])

        with _tag_scope_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _enforcer(lambda o, a: True, record)):
            result = _attempt_update(m, {"tags": ["ok"]}, {"tokens": ["SYSTEM_USER"]})

        assert getattr(result, "success", None) is True
        m.asset_table.update_item.assert_called_once()
