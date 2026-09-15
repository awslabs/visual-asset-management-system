# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-020 (S2-BACKEND-062, HIGH): the database pre-filter must not evaluate asset criteria
against database rows.

`DatabaseAccessManager` stamped ``object__type: "asset"`` onto a DATABASE row and called
``CasbinEnforcer.enforce()`` on it, in both of its access paths (search.py:321 and :374). For a role
scoped by an asset-only field -- ``assetName``, ``assetType``, ``tags`` -- the database row carries no
such attribute, the empty-string defaulting in ``handlers/authz/__init__.py`` makes an unset field match
nothing, and the database was dropped from ``accessible_databases``. The user then got zero search
results with no error.

The owner's directive: *"Code should evaluate all the asset fields that we have Casbin fields for but
should be object type asset (not database) for those records"* -- i.e. keep evaluating every asset-type
field where the record really is an asset, and stop enforcing asset criteria against database rows. So
the property under test is the OBJECT TYPE handed to Casbin, which is exactly what the fix changes. The
asset fields keep being evaluated against real asset documents in
``DualIndexResponseProcessor._is_hit_authorized``, which types each search hit ``asset`` and carries
``databaseId``, ``assetName``, ``assetType`` and ``tags``.

## Why this asserts the argument rather than the verdict

No real enforcer is reachable from a unit test. The root conftest registers ``handlers.authz`` as an
empty stand-in with no ``CasbinEnforcer`` at all (conftest.py:289), so the ``search_module`` fixture below
supplies one as a ``MagicMock``, and a MagicMock's ``enforce()`` returns a truthy Mock. A test written
against the VERDICT therefore passes whatever the code does -- it would report the fix as working before
it was written. Using the stub deliberately as a SPY and asserting the document it received is both
non-vacuous and immune to that.

``TestTagScopedRoleReachesItsDatabases`` covers the other half -- that a database a tag-scoped role IS
entitled to still comes back -- and needs a verdict, so it substitutes a stand-in that models one
role's policy rather than answering True to everything. Object-type assertions alone would be satisfied
by a fix that excluded every database.

The module is loaded from its file path because the root conftest registers mock ``handlers``/``common``
packages that shadow the real ones -- the same approach as ``test_searchable_fields.py`` and the
fileIndexer tests. Importing ``handlers.search.search`` normally yields
``tests/mocks/handlers/search/search.py``, which defines one helper function and a ``SearchHandler``
class -- no ``DatabaseAccessManager`` and no module-level ``lambda_handler``.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def search_module():
    """The real search module, loaded by file path with boto3 stubbed."""
    saved = {
        name: sys.modules.get(name)
        for name in ("handlers.auth", "handlers.authz", "common.dynamodb")
    }

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_prefilter", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


class _EnforcerSpy:
    """Stands in for CasbinEnforcer and records every document passed to enforce()."""

    documents = []

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, document, action):
        # Copy: the caller mutates the same dict afterwards, so a reference would record the
        # post-call state and the assertion would read whatever happened last.
        _EnforcerSpy.documents.append(dict(document))
        return True


class _TagScopedRoleEnforcer:
    """Stands in for CasbinEnforcer holding ONE role's grants: a ``database``-typed GET on a single
    databaseId plus an ``asset``-typed GET restricted to ``tags is_one_of ['public']``. That pairing is
    the shape of every shipped permission template that grants asset GET -- each pairs its `asset`
    constraint with a `database` constraint over the same scope
    (``documentation/permissionsTemplates/*.json``).

    It reproduces the two behaviours of ``CasbinEnforcerService.enforce`` that decide this defect:

    * the document is reduced to the fields valid for ITS OWN ``object__type``
      (``_scrub_object_fields``), so an asset rule sees nothing else, and
    * a constraint field the record does not carry is defaulted to an empty value rather than being
      skipped, so a rule referencing it compares against ``""``/``[]`` and matches nothing.

    A database row carries no ``tags``, so the asset rule can never match one -- which is why this
    role's pre-filter returned no databases at all while the row was typed ``asset``.
    """

    GRANTED_DATABASE_ID = "smoke-db"
    GRANTED_TAG = "public"

    # Mirrors CONSTRAINT_OBJECT_TYPE_FIELDS in backend/backend/common/constants.py.
    _FIELDS_BY_OBJECT_TYPE = {
        "database": ("databaseId",),
        "asset": ("databaseId", "assetName", "assetType", "tags"),
    }

    documents = []

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, document, action):
        _TagScopedRoleEnforcer.documents.append(dict(document))
        fields = self._FIELDS_BY_OBJECT_TYPE.get(document.get("object__type"))
        if fields is None:
            return False
        scrubbed = {
            field: document.get(field, [] if field == "tags" else "") for field in fields
        }
        if document["object__type"] == "database":
            return scrubbed["databaseId"] == self.GRANTED_DATABASE_ID
        return self.GRANTED_TAG in scrubbed["tags"]


@pytest.fixture
def spy(search_module):
    _EnforcerSpy.documents = []
    search_module.CasbinEnforcer = _EnforcerSpy
    return _EnforcerSpy


@pytest.fixture
def tag_scoped_role(search_module):
    _TagScopedRoleEnforcer.documents = []
    search_module.CasbinEnforcer = _TagScopedRoleEnforcer
    return _TagScopedRoleEnforcer


def _db_row(database_id):
    """A database row as DynamoDB returns it from a scan (attribute-value encoded)."""
    return {
        "databaseId": {"S": database_id},
        "description": {"S": "a database, not an asset"},
        "dateCreated": {"S": "2026-01-01T00:00:00Z"},
    }


_DB_ROW_SERIALIZED = _db_row("smoke-db")
_DB_ROW_PLAIN = {
    "databaseId": "smoke-db",
    "description": "a database, not an asset",
    "dateCreated": "2026-01-01T00:00:00Z",
}

_CLAIMS = {"tokens": ["tag-scoped-user"], "roles": ["tag-scoped-role"]}


def _stub_scan(search_module, pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    search_module.dynamodb_client.get_paginator.return_value = paginator


@pytest.mark.unit
class TestGetAccessibleDatabases:
    """The list path: search.py:374."""

    def test_the_enforcement_point_is_reached(self, search_module, spy):
        """Control. Every assertion below is about the document Casbin received; if enforce() were
        never called, an assertion that no document was typed 'asset' would pass while proving
        nothing. This pins that the scan is read, the row is deserialized, and the row is returned."""
        _stub_scan(search_module, [{"Items": [_DB_ROW_SERIALIZED]}])
        result = search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
        assert result == ["smoke-db"]
        assert len(spy.documents) == 1
        assert spy.documents[0]["databaseId"] == "smoke-db"

    def test_no_enforcement_without_tokens(self, search_module, spy):
        """Second control, and a fail-closed check in its own right: with no tokens the code must not
        consult Casbin, and must not accumulate the database either."""
        _stub_scan(search_module, [{"Items": [_DB_ROW_SERIALIZED]}])
        assert search_module.DatabaseAccessManager.get_accessible_databases({"tokens": []}) == []
        assert spy.documents == []

    def test_database_rows_are_not_typed_as_assets(self, search_module, spy):
        _stub_scan(search_module, [{"Items": [_DB_ROW_SERIALIZED]}])
        search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
        assert [d.get("object__type") for d in spy.documents] == ["database"]


@pytest.mark.unit
class TestGetAccessibleDatabase:
    """The single-database path: search.py:321. Covered separately because the fix has to change both
    -- correcting only the list path leaves every single-database lookup still misclassified, and the
    two are 50 lines apart in the same class."""

    def test_the_enforcement_point_is_reached(self, search_module, spy):
        search_module.database_storage_table = MagicMock()
        search_module.database_storage_table.get_item.return_value = {"Item": dict(_DB_ROW_PLAIN)}
        result = search_module.DatabaseAccessManager.get_accessible_database("smoke-db", _CLAIMS)
        assert result == "smoke-db"
        assert len(spy.documents) == 1

    def test_database_row_is_not_typed_as_asset(self, search_module, spy):
        search_module.database_storage_table = MagicMock()
        search_module.database_storage_table.get_item.return_value = {"Item": dict(_DB_ROW_PLAIN)}
        search_module.DatabaseAccessManager.get_accessible_database("smoke-db", _CLAIMS)
        assert [d.get("object__type") for d in spy.documents] == ["database"]


@pytest.mark.unit
class TestTagScopedRoleReachesItsDatabases:
    """The permitted half. The assertions above are all about the object type handed to Casbin, so a
    fix that dropped EVERY database would satisfy them. These pin that the database a tag-scoped role
    is entitled to still comes back, and that one it has no grant on still does not."""

    def test_the_granted_database_is_returned(self, search_module, tag_scoped_role):
        """The regression itself: with the row typed 'asset' the role's tag rule sees tags=[] and this
        returns [], which is the zero-search-results symptom."""
        _stub_scan(search_module, [{"Items": [_db_row("smoke-db")]}])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == ["smoke-db"]

    def test_an_ungranted_database_is_still_excluded(self, search_module, tag_scoped_role):
        """Negative control: the pre-filter is still a filter. 'other-db' is outside the role's
        database constraint, so it must be dropped."""
        _stub_scan(search_module, [{"Items": [_db_row("other-db")]}])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == []

    def test_a_mixed_page_keeps_only_the_granted_database(self, search_module, tag_scoped_role):
        _stub_scan(
            search_module, [{"Items": [_db_row("other-db"), _db_row("smoke-db")]}]
        )
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == ["smoke-db"]

    def test_the_single_database_lookup_returns_the_granted_database(
        self, search_module, tag_scoped_role
    ):
        search_module.database_storage_table = MagicMock()
        search_module.database_storage_table.get_item.return_value = {"Item": dict(_DB_ROW_PLAIN)}
        assert (
            search_module.DatabaseAccessManager.get_accessible_database("smoke-db", _CLAIMS)
            == "smoke-db"
        )

    def test_the_single_database_lookup_denies_an_ungranted_database(
        self, search_module, tag_scoped_role
    ):
        search_module.database_storage_table = MagicMock()
        search_module.database_storage_table.get_item.return_value = {
            "Item": dict(_DB_ROW_PLAIN, databaseId="other-db")
        }
        assert (
            search_module.DatabaseAccessManager.get_accessible_database("other-db", _CLAIMS) is None
        )
