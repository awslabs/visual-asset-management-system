# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch, MagicMock

from backend.backend.common.tagScope import (
    GLOBAL_SCOPE, normalize_scope, is_visible_in_scope, verify_database_exists,
    name_used_by_any_database,
)


@pytest.mark.unit
class TestNormalizeScope:
    def test_missing_becomes_global(self):
        assert normalize_scope(None) == GLOBAL_SCOPE
        assert normalize_scope("") == GLOBAL_SCOPE

    def test_global_sentinel_preserved(self):
        assert normalize_scope("GLOBAL") == GLOBAL_SCOPE

    def test_database_id_preserved(self):
        assert normalize_scope("factory-db") == "factory-db"


@pytest.mark.unit
class TestIsVisibleInScope:
    def test_global_visible_everywhere(self):
        assert is_visible_in_scope(None, requested_database_id="factory-db") is True
        assert is_visible_in_scope("GLOBAL", requested_database_id=None) is True

    def test_scoped_visible_only_in_own_db_or_all(self):
        assert is_visible_in_scope("factory-db", requested_database_id="factory-db") is True
        assert is_visible_in_scope("factory-db", requested_database_id="hospital-db") is False
        # requested None => "all" admin view: everything visible
        assert is_visible_in_scope("factory-db", requested_database_id=None) is True

    def test_global_only_request_excludes_scoped(self):
        assert is_visible_in_scope("factory-db", requested_database_id=None, global_only=True) is False
        assert is_visible_in_scope("GLOBAL", requested_database_id=None, global_only=True) is True


@pytest.mark.unit
class TestVerifyDatabaseExists:
    def test_global_skips_lookup(self):
        table = MagicMock()
        assert verify_database_exists("GLOBAL", table) is True
        table.get_item.assert_not_called()

    def test_existing_database_ok(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "factory-db"}}
        assert verify_database_exists("factory-db", table) is True

@pytest.mark.unit
class TestNameUsedByAnyDatabase:
    def test_returns_true_when_a_database_uses_name(self):
        table = MagicMock()
        table.query.return_value = {"Items": [{"databaseId": "factory-db", "tagName": "Status"}]}
        assert name_used_by_any_database(table, "tagNameIndex", "tagName", "Status") is True

    def test_returns_false_when_only_global_uses_name(self):
        table = MagicMock()
        table.query.return_value = {"Items": [{"databaseId": "GLOBAL", "tagName": "Status"}]}
        assert name_used_by_any_database(table, "tagNameIndex", "tagName", "Status") is False

    def test_returns_false_when_name_unused(self):
        table = MagicMock()
        table.query.return_value = {"Items": []}
        assert name_used_by_any_database(table, "tagNameIndex", "tagName", "Status") is False

    def test_queries_the_named_gsi(self):
        table = MagicMock()
        table.query.return_value = {"Items": []}
        name_used_by_any_database(table, "tagTypeNameIndex", "tagTypeName", "Custom")
        assert table.query.call_args.kwargs["IndexName"] == "tagTypeNameIndex"


class TestVerifyDatabaseExistsMissing:
    def test_missing_database_raises(self):
        # Reference the exception through the module under test so the asserted
        # class is the exact object raised. models.common and
        # backend.backend.models.common load from the same file but as two
        # distinct module objects (distinct classes), so importing the exception
        # via the second name would not match what tagScope raises. This mirrors
        # the repo convention (e.g. tests/handlers/assets/test_createAsset_prefix_uniqueness.py
        # uses m.VAMSGeneralErrorResponse).
        from backend.backend.common import tagScope
        table = MagicMock()
        table.get_item.return_value = {}
        with pytest.raises(tagScope.VAMSGeneralErrorResponse):
            verify_database_exists("nope-db", table)
