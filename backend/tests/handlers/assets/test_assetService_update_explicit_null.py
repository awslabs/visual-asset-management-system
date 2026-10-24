# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset update: a field sent as JSON `null` is left unchanged, exactly as an omitted field is.

`UpdateAssetRequestModel` declares every editable field `Optional[...] = None`, so the parsed
model holds `None` both for a field the body omitted and for one it sent as `null`; only the set
of fields forwarded to `update_asset` tells them apart. A `null` that reaches `update_asset` is
applied as a value: `assetName`, `description` and `isDistributable` are written to the record as
NULL (the asset's GET then no longer fits `AssetResponseModel` and falls back to the raw record),
and a `null` `tags` is iterated as a list and surfaces as a 500.

The cases drive `handle_put_request`, so the real `parse`, the real request model and the real
forwarding between them are under test. Each `null` travels beside one non-null field, because a
body whose fields are all `null` is refused by the model's "at least one field" rule before
anything is forwarded (pinned below as a control).
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_assetService_authz_fail_closed import _invoke, _wire
from tests.handlers.assets.test_assetService_update_tag_scope import (
    _existing_asset,
    _written_attributes,
)


# `_wire` serves the stored record from `_existing_asset`: assetName "N1", description "d1",
# isDistributable True, tags [].
_CHANGED_DESCRIPTION = "a changed description"


def _put(body, stored_tags=None):
    """PUT `body` through handle_put_request as a permitted caller.

    `stored_tags`, when given, replaces the stored record's empty tag list. Returns the
    response, the enforcer spy, and the loaded module, whose `asset_table` and
    `write_asset_history_record` are the mocks `_wire` installed.
    """
    m, spy, undo = _wire()
    if stored_tags is not None:
        stored = {**_existing_asset(), "tags": list(stored_tags)}
        m.get_asset_details = MagicMock(
            side_effect=lambda *a, **kw: {**stored, "tags": list(stored["tags"])}
        )
    try:
        response = _invoke(m, "handle_put_request", "PUT", "", body)
    finally:
        undo()
    return response, spy, m


@pytest.mark.unit
class TestExplicitNullLeavesTheFieldUnchanged:
    @pytest.mark.parametrize(
        "null_field,other_field,other_value",
        [
            ("assetName", "description", _CHANGED_DESCRIPTION),
            ("description", "assetName", "renamed"),
            ("isDistributable", "description", _CHANGED_DESCRIPTION),
            ("tags", "description", _CHANGED_DESCRIPTION),
        ],
        ids=["assetName", "description", "isDistributable", "tags"],
    )
    def test_a_null_field_is_not_written(self, null_field, other_field, other_value):
        response, _spy, m = _put({other_field: other_value, null_field: None})

        assert response["statusCode"] == 200, (
            f"a null {null_field} beside a valid {other_field} was refused: {response}"
        )
        assert _written_attributes(m.asset_table) == {other_field: other_value}, (
            f"the explicit null for {null_field} reached the asset record as a value"
        )

    def test_the_history_snapshot_keeps_the_stored_value_of_a_null_field(self):
        response, _spy, m = _put({"description": _CHANGED_DESCRIPTION, "assetName": None})

        assert response["statusCode"] == 200, response
        snapshot = m.write_asset_history_record.call_args.args[4]
        assert snapshot["assetName"] == "N1"
        assert snapshot["description"] == _CHANGED_DESCRIPTION

    def test_null_tags_are_not_a_tag_change(self):
        """A null tag list is neither iterated, nor read as an empty list, nor authorized as a
        retag. The stored list is non-empty, so reading `null` as `[]` would be a tag change."""
        response, spy, m = _put(
            {"description": _CHANGED_DESCRIPTION, "tags": None}, stored_tags=["keep"]
        )

        assert response["statusCode"] == 200, (
            f"a null tag list surfaced as {response['statusCode']}: {response}"
        )
        assert _written_attributes(m.asset_table) == {"description": _CHANGED_DESCRIPTION}
        assert [call["action"] for call in spy.calls] == ["PUT"], (
            "a null tag list reached the tag-change authorization gate, which only a changed "
            f"tag list evaluates: {spy.calls}"
        )
        assert m.write_asset_history_record.call_args.args[4]["tags"] == ["keep"]


@pytest.mark.unit
class TestExplicitValuesStillApply:
    """Positive controls: leaving nulls out must not leave out a falsy value, and must not change
    how a body with nothing to update is refused."""

    def test_false_and_empty_values_are_written(self):
        """`false` and `[]` are values rather than absences; a truthiness filter drops both."""
        response, _spy, m = _put(
            {
                "assetName": "renamed",
                "description": _CHANGED_DESCRIPTION,
                "isDistributable": False,
                "tags": [],
            }
        )

        assert response["statusCode"] == 200, response
        assert _written_attributes(m.asset_table) == {
            "assetName": "renamed",
            "description": _CHANGED_DESCRIPTION,
            "isDistributable": False,
            "tags": [],
        }

    def test_a_body_of_only_nulls_is_refused_and_writes_nothing(self):
        response, _spy, m = _put({"assetName": None, "tags": None})

        assert response["statusCode"] == 400, response
        assert (
            "At least one field must be provided for update"
            in json.loads(response["body"])["message"]
        )
        m.asset_table.update_item.assert_not_called()
