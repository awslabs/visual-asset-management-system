# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A tag type marked required but holding no tags does not constrain an asset.

Nothing can satisfy such a tag type — the asset forms and the CLI can only offer tags that
exist — so treating it as required would make an asset uncreatable and un-editable. The rule
applies to GLOBAL and database-scoped tag types alike, on the create AND update paths.
"""

import sys
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_tag_scope import (
    _load as _load_create_asset,
    _make_tag_table,
    _make_tag_type_table,
    _wire_create,
    _request_model,
)
from tests.handlers.assets.test_assetService_update_tag_scope import (
    _load_asset_service,
    _wire_create_for_scope,
    _wire_update,
    _existing_asset,
)


def _scoped_tables(ddb_resource):
    ca = _load_create_asset()
    tag_table = _make_tag_table(ddb_resource)
    tag_type_table = _make_tag_type_table(ddb_resource)
    ca.tag_table = tag_table
    ca.tag_type_table = tag_type_table
    return ca, tag_table, tag_type_table


@pytest.mark.unit
class TestRequiredTagTypeWithoutTags:
    def test_empty_required_global_tag_type_does_not_constrain(self, ddb_resource):
        ca, _, tag_type_table = _scoped_tables(ddb_resource)
        tag_type_table.put_item(Item={"databaseId": "GLOBAL", "tagTypeName": "Clearance",
                                      "required": "True", "description": "d"})

        assert ca.get_required_tag_types("db-a") == []
        assert ca.verify_all_required_tags_satisfied([], "db-a") is True

    def test_empty_required_database_tag_type_does_not_constrain(self, ddb_resource):
        ca, _, tag_type_table = _scoped_tables(ddb_resource)
        tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                      "required": "True", "description": "d"})

        assert ca.get_required_tag_types("db-a") == []
        assert ca.verify_all_required_tags_satisfied([], "db-a") is True

    def test_a_populated_required_tag_type_is_still_enforced(self, ddb_resource):
        """Control: the empty-type exemption must not disable the required rule itself."""
        ca, tag_table, tag_type_table = _scoped_tables(ddb_resource)
        tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Empty",
                                      "required": "True", "description": "d"})
        tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                      "required": "True", "description": "d"})
        tag_table.put_item(Item={"databaseId": "db-a", "tagName": "press",
                                 "tagTypeName": "Line", "description": "d"})

        assert ca.get_required_tag_types("db-a") == ["Line"]

        with pytest.raises(ValueError) as missing:
            ca.verify_all_required_tags_satisfied([], "db-a")
        # Only the satisfiable type is reported; naming the empty one would ask for the impossible.
        assert "Line" in str(missing.value)
        assert "Empty" not in str(missing.value)

        assert ca.verify_all_required_tags_satisfied(["press"], "db-a") is True

    def test_a_required_tag_type_whose_only_tag_was_deleted_stops_constraining(self, ddb_resource):
        """Deleting a required type's last tag leaves it unsatisfiable, so it stops applying."""
        ca, tag_table, tag_type_table = _scoped_tables(ddb_resource)
        tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                      "required": "True", "description": "d"})
        tag_table.put_item(Item={"databaseId": "db-a", "tagName": "press",
                                 "tagTypeName": "Line", "description": "d"})
        assert ca.get_required_tag_types("db-a") == ["Line"]

        tag_table.delete_item(Key={"databaseId": "db-a", "tagName": "press"})
        assert ca.get_required_tag_types("db-a") == []
        assert ca.verify_all_required_tags_satisfied([], "db-a") is True

    def test_create_asset_succeeds_with_no_tags(self, ddb_resource):
        ca, tag_table, tag_type_table = _scoped_tables(ddb_resource)
        tag_type_table.put_item(Item={"databaseId": "GLOBAL", "tagTypeName": "Clearance",
                                      "required": "True", "description": "d"})
        tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                      "required": "True", "description": "d"})
        _wire_create(ca, tag_table, tag_type_table)

        response = ca.create_asset(_request_model(ca, []), {"tokens": ["user1"]})

        assert response.assetId == "asset-1"
        ca.save_asset_details.assert_called_once()


@pytest.mark.unit
class TestUpdateWithEmptyRequiredTagType:
    def test_update_succeeds_when_the_required_tag_type_has_no_tags(self, ddb_resource):
        saved = sys.modules.get("handlers.assets.createAsset")
        try:
            ca, tag_table, tag_type_table = _scoped_tables(ddb_resource)
            tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                     "tagTypeName": "System", "description": "d"})
            tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                          "required": "True", "description": "d"})
            _wire_create_for_scope(ca, tag_table, tag_type_table)

            m = _load_asset_service()
            _wire_update(m, _existing_asset("db-a"))

            # Adding a tag changes the set, which is what triggers the required-tag check.
            result = m.update_asset(
                "db-a", "asset-1",
                {"tags": ["reviewed"]},
                {"tokens": ["u1"]},
            )

            assert result.success is True
            m.asset_table.put_item.assert_called_once()
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved
            else:
                sys.modules.pop("handlers.assets.createAsset", None)

    def test_update_is_still_blocked_by_a_populated_required_tag_type(self, ddb_resource):
        saved = sys.modules.get("handlers.assets.createAsset")
        try:
            ca, tag_table, tag_type_table = _scoped_tables(ddb_resource)
            tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                     "tagTypeName": "System", "description": "d"})
            tag_type_table.put_item(Item={"databaseId": "db-a", "tagTypeName": "Line",
                                          "required": "True", "description": "d"})
            tag_table.put_item(Item={"databaseId": "db-a", "tagName": "press",
                                     "tagTypeName": "Line", "description": "d"})
            _wire_create_for_scope(ca, tag_table, tag_type_table)

            m = _load_asset_service()
            _wire_update(m, _existing_asset("db-a"))

            with pytest.raises(Exception) as rejected:
                m.update_asset(
                    "db-a", "asset-1",
                    {"tags": ["reviewed"]},
                    {"tokens": ["u1"]},
                )
            assert type(rejected.value).__name__ == "VAMSGeneralErrorResponse"
            m.asset_table.put_item.assert_not_called()
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved
            else:
                sys.modules.pop("handlers.assets.createAsset", None)
