# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import ast
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.backend.common.tagScope import (
    GLOBAL_SCOPE, normalize_scope, verify_database_exists,
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
class TestNoPerItemVisibilityRule:
    """A listing's scope is decided by the queried partition, not by a per-item predicate.

    ``?databaseId=X`` queries partition X alone and returns no GLOBAL rows
    (tests/handlers/tags/test_tagServiceScope.py pins that on the handler), so a helper
    answering "is this row visible for scope X" with a global+X union describes semantics
    the listings do not have. The union rule belongs to asset tag resolution only, where
    createAsset.py reads both scopes explicitly.
    """

    def test_no_visibility_predicate_is_exported(self):
        from backend.backend.common import tagScope
        assert not hasattr(tagScope, "is_visible_in_scope")

    def test_module_functions_are_only_the_ones_handlers_import(self):
        # Guards the shape rather than one name: a new module-level helper here has to be
        # added deliberately and checked against the partition-only listing rule.
        from backend.backend.common import tagScope
        defined_here = {
            name for name, value in vars(tagScope).items()
            if callable(value)
            and not name.startswith("_")
            and getattr(value, "__module__", None) == tagScope.__name__
        }
        assert defined_here == {
            "normalize_scope", "verify_database_exists", "name_used_by_any_database",
        }


@pytest.mark.unit
class TestHandlerImportSurface:
    """Positive control: tagScope still satisfies every name production code imports from it.

    A missing name here is an ImportError at Lambda cold start, so the guard reads the import
    statements out of the tree rather than naming the importers: the set of modules that import
    from tagScope may grow, but every name any of them takes has to exist.
    """

    def test_every_production_import_of_tagScope_resolves(self):
        from backend.backend.common import tagScope

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        scanned = 0
        requested = []
        for source_file in backend_root.rglob("*.py"):
            try:
                tree = ast.parse(source_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            scanned += 1
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("tagScope"):
                    for alias in node.names:
                        requested.append((source_file.name, alias.name))

        # Both floors keep a walk that matched nothing from passing vacuously.
        assert scanned >= 50, f"only parsed {scanned} backend modules — the walk is broken"
        assert requested, "found no production import of tagScope — the walk is broken"

        missing = [(f, n) for f, n in requested if not hasattr(tagScope, n)]
        assert missing == [], f"tagScope no longer provides imported names: {missing}"

    def test_every_imported_symbol_resolves_and_works(self):
        # Resolving is not enough — each surviving symbol still has to behave.
        from backend.backend.common.tagScope import (
            GLOBAL_SCOPE as sentinel,
            normalize_scope as normalize,
            verify_database_exists as verify,
            name_used_by_any_database as name_used,
        )
        assert sentinel == "GLOBAL"
        assert normalize("factory-db") == "factory-db"

        database_table = MagicMock()
        database_table.get_item.return_value = {"Item": {"databaseId": "factory-db"}}
        assert verify("factory-db", database_table) is True

        tag_table = MagicMock()
        tag_table.query.return_value = {"Items": [{"databaseId": "GLOBAL", "tagName": "Status"}]}
        assert name_used(tag_table, "tagNameIndex", "tagName", "Status") is False


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
