# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset rename is authorized against the renamed record as well (#369).

A constraint can be keyed on `assetName` (`assetName starts_with PROJ-`), so a check made only
against the stored record lets a caller rename an asset out of the constraint that scopes them.
`update_asset` therefore evaluates a rename the way it evaluates a tag change: GET on the stored
record, and GET and PUT on the record as it will be written, with the new name and the new tag
list together.

The controls are the callers an over-tightened check would lock out: a role allowed on both
names, a full-object PUT that resubmits the stored name, and a combined rename and retag whose
end state is allowed although each half alone is not.
"""

import json
from unittest.mock import patch

import pytest

from tests.handlers.assets.test_assetService_tag_mutation_authz import (  # noqa: E402
    _DB,
    _ASSET,
    _assert_denied,
    _attempt_update,
    _load_asset_service,
    _name_evaluations,
    _recording_enforcer,
    _refusals,
    _tag_existence_validation_stubbed,
    _wire,
    _written_attributes,
)


def _allow_proj_names(obj, action):
    """An ALLOW rule scoped `assetName starts_with 'PROJ-'`."""
    return str(obj.get("assetName") or "").startswith("PROJ-")


def _deny_get_on_hidden_names(obj, action):
    """A visibility deny: `assetName starts_with 'hidden-'` refuses GET and permits every write."""
    return not (action == "GET" and str(obj.get("assetName") or "").startswith("hidden-"))


def _allow_end_states_only(obj, action):
    """Allows (PROJ-*, no tags) and (OTHER-*, tag 'team'), and nothing in between."""
    name = str(obj.get("assetName") or "")
    tags = obj.get("tags") or []
    return (name.startswith("PROJ-") and not tags) or (name.startswith("OTHER-") and "team" in tags)


@pytest.mark.unit
class TestRenameIsAuthorizedOnTheRenamedRecord:
    def test_a_rename_out_of_the_callers_name_scope_is_refused_with_403_and_not_written(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")
        event = {
            "requestContext": {
                "http": {"method": "PUT", "path": f"/database/{_DB}/assets/{_ASSET}"}
            },
            "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
            "queryStringParameters": None,
            "body": json.dumps({"assetName": "OTHER-1"}),
        }
        saved_claims = getattr(m, "claims_and_roles", {})
        m.claims_and_roles = {"tokens": ["u1"]}
        try:
            with _tag_existence_validation_stubbed(), \
                    patch.object(m, "CasbinEnforcer", _recording_enforcer(_allow_proj_names, record)):
                response = m.handle_put_request(event)
        finally:
            m.claims_and_roles = saved_claims

        assert response["statusCode"] == 403, f"a refused rename answered {response}"
        m.asset_table.update_item.assert_not_called()
        m.write_asset_history_record.assert_not_called()
        assert ("PUT", "OTHER-1") in _name_evaluations(record) or (
            "GET", "OTHER-1") in _name_evaluations(record), (
            f"the renamed record was never evaluated: {sorted(_name_evaluations(record), key=str)}"
        )

    def test_a_rename_allowed_on_both_names_is_written(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _recording_enforcer(_allow_proj_names, record)):
            result = _attempt_update(m, {"assetName": "PROJ-2"})

        assert getattr(result, "success", None) is True, f"an allowed rename was refused: {result}"
        assert _written_attributes(m.asset_table)["assetName"] == "PROJ-2"
        evaluated = _name_evaluations(record)
        for pair in (("GET", "PROJ-1"), ("GET", "PROJ-2"), ("PUT", "PROJ-2")):
            assert pair in evaluated, f"{pair} was not evaluated: {sorted(evaluated, key=str)}"

    def test_renaming_into_a_name_the_caller_cannot_see_is_refused(self):
        """A GET-only deny is invisible to a PUT check, so the renamed record is checked for GET."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="model-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_hidden_names, record)):
            result = _attempt_update(m, {"assetName": "hidden-1"})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)
        assert _refusals(record, _deny_get_on_hidden_names), "nothing was refused"

    def test_renaming_out_of_a_name_the_caller_cannot_see_is_refused(self):
        """The caller must already reach the asset they rename: GET on the stored record."""
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="hidden-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_hidden_names, record)):
            result = _attempt_update(m, {"assetName": "model-1"})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)
        assert ("GET", "hidden-1") in _name_evaluations(record)


@pytest.mark.unit
class TestEditsThatKeepTheNameStayUngated:
    def test_a_full_object_put_that_resubmits_the_stored_name_is_not_re_checked(self):
        """The web form sends the name with every edit; an unchanged name is not a rename.

        This constraint refuses GET on the stored name, so a rename check that ignored whether
        the name changed would make every description edit of this asset fail.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="hidden-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_deny_get_on_hidden_names, record)):
            result = _attempt_update(m, {"assetName": "hidden-1", "description": "edited"})

        assert getattr(result, "success", None) is True, (
            f"resubmitting the stored name was treated as a rename: {result}"
        )
        assert not _refusals(record, _deny_get_on_hidden_names)

    def test_a_system_user_cross_call_rename_still_writes(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer", _recording_enforcer(lambda o, a: True, record)):
            result = _attempt_update(m, {"assetName": "OTHER-1"}, {"tokens": ["SYSTEM_USER"]})

        assert getattr(result, "success", None) is True
        m.asset_table.update_item.assert_called_once()


@pytest.mark.unit
class TestRenameAndRetagTogether:
    def test_the_end_state_is_what_is_authorized(self):
        """Renaming and retagging in one request is checked on the stored and the final record.

        The constraint allows the stored state (PROJ-1, no tags) and the final state (OTHER-1,
        'team') but neither half-way state, so a check of an intermediate record would refuse an
        edit whose result the caller is allowed to write.
        """
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_end_states_only, record)):
            result = _attempt_update(m, {"assetName": "OTHER-1", "tags": ["team"]})

        assert getattr(result, "success", None) is True, (
            f"an allowed rename and retag was refused: {result}, refused "
            f"{_refusals(record, _allow_end_states_only)}"
        )
        written = _written_attributes(m.asset_table)
        assert written["assetName"] == "OTHER-1" and written["tags"] == ["team"]
        final = {(call["action"], call["object"].get("assetName"), tuple(call["object"].get("tags") or ()))
                 for call in record}
        assert ("PUT", "OTHER-1", ("team",)) in final and ("GET", "OTHER-1", ("team",)) in final

    def test_a_rename_and_retag_whose_end_state_is_refused_is_not_written(self):
        record = []
        m = _wire(_load_asset_service(), existing_tags=[], asset_name="PROJ-1")

        with _tag_existence_validation_stubbed(), \
                patch.object(m, "CasbinEnforcer",
                             _recording_enforcer(_allow_end_states_only, record)):
            result = _attempt_update(m, {"assetName": "OTHER-1", "tags": ["other"]})

        m.asset_table.update_item.assert_not_called()
        _assert_denied(result)
