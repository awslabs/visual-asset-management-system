# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The accessible-database pre-filter admits exactly the accessible databases.

Both search endpoints restrict every index query to the ids that
``DatabaseAccessManager.get_accessible_databases`` returns. The deployed indexes map
``str_databaseid`` as analyzed ``text`` with a ``keyword`` subfield
(``infra/lib/nestedStacks/searchAndIndexing/constructs/schemaDeploy/deployschema.ts``), and the
standard analyzer lowercases and splits on hyphens. A quoted phrase on the analyzed field is
therefore a token-sequence match, not an id match: ``str_databaseid:("smoke-db")`` looks for
[smoke, db], which ``smoke-db-2``, ``old-smoke-db`` and ``Smoke-DB`` all carry. Documents from those
databases entered the OpenSearch window ahead of the per-hit Casbin check, using up page capacity,
and were counted in the aggregations, which are computed over the query scope and are not
post-filtered.

The property under test is the clause the builders emit, on both builders and both indexes:

* the restriction is a ``terms`` query on ``str_databaseid.keyword`` carrying the accessible ids
  verbatim, which OpenSearch matches exactly, case and hyphens included;
* no builder-generated ``query_string`` names the analyzed ``str_databaseid`` field; and
* an empty accessible list admits no document and names no database. A sentinel id is itself a
  database id: ``NOACCESSDATABASE`` satisfies ``^[-_a-zA-Z0-9]{3,63}$``, and as an analyzed phrase
  it admitted any database whose id holds that token.

The controls pin what stays as it is: the simple-search ``databaseId`` term filter, caller-supplied
filters, and the detector the absence assertions rely on.

The module is loaded from its file path because the root conftest registers mock
``handlers``/``common`` packages that shadow the real ones -- the same approach as
``test_database_prefilter_object_type.py``.
"""

import importlib.util
import json
import os
import re
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
                "search_under_test_db_access_filter", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


# Mixed case and hyphens, the two ways the analyzed field fails to match an id exactly, plus an
# underscore, which the terms list must also carry verbatim.
_ACCESSIBLE = ["smoke-db", "Plant_A-01"]

_KEYWORD_RESTRICTION = {"terms": {"str_databaseid.keyword": ["smoke-db", "Plant_A-01"]}}

_CLAIMS = {"tokens": ["search-user"], "roles": ["search-role"]}

_BUILDERS = ("simple", "complex")
_INDEXES = ("asset", "file")

# `str_databaseid` followed by the field separator, i.e. the analyzed field rather than its
# `.keyword` subfield.
_ANALYZED_FIELD = re.compile(r"\bstr_databaseid\s*:")


def _clause(module, builder_kind, accessible, index_type, **request_kwargs):
    """The query clause one builder emits for one index, given the accessible ids."""
    manager = module.DatabaseAccessManager()
    if builder_kind == "simple":
        builder = module.SimpleSearchQueryBuilder(manager)
        request = module.SimpleSearchRequestModel(**request_kwargs)
        return builder._build_simple_query_clause(request, accessible, index_type)
    builder = module.DualIndexQueryBuilder(manager)
    request = module.SearchRequestModel(**request_kwargs)
    return builder._build_query_clause(request, accessible, index_type)


def _filters(clause):
    return clause.get("bool", {}).get("filter", [])


def _database_clauses(clause):
    """The filter clauses that restrict on str_databaseid, in either field form."""
    return [item for item in _filters(clause) if "str_databaseid" in json.dumps(item)]


def _analyzed_field_queries(node):
    """Every query_string query anywhere under `node` that names the analyzed field."""
    found = []
    if isinstance(node, dict):
        query_string = node.get("query_string")
        if isinstance(query_string, dict) and _ANALYZED_FIELD.search(query_string.get("query", "")):
            found.append(query_string["query"])
        for value in node.values():
            found.extend(_analyzed_field_queries(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_analyzed_field_queries(item))
    return found


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", _BUILDERS)
@pytest.mark.parametrize("index_type", _INDEXES)
class TestAccessibleDatabasesMatchExactly:
    """The restriction for a non-empty accessible list."""

    def test_the_restriction_is_a_terms_query_on_the_keyword_subfield(
        self, search_module, builder_kind, index_type
    ):
        clause = _clause(search_module, builder_kind, list(_ACCESSIBLE), index_type)
        assert _database_clauses(clause) == [_KEYWORD_RESTRICTION], (
            f"{builder_kind}/{index_type} database restriction: "
            f"{json.dumps(_database_clauses(clause))}"
        )

    def test_no_generated_query_names_the_analyzed_field(
        self, search_module, builder_kind, index_type
    ):
        """Asserted with a keyword query as well, so the general-search clauses are built too."""
        clause = _clause(search_module, builder_kind, list(_ACCESSIBLE), index_type, query="pump")
        assert _analyzed_field_queries(clause) == []


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", _BUILDERS)
@pytest.mark.parametrize("index_type", _INDEXES)
class TestNoAccessibleDatabase:
    """An empty accessible list admits no document."""

    def test_the_restriction_matches_no_document(self, search_module, builder_kind, index_type):
        clause = _clause(search_module, builder_kind, [], index_type)
        assert {"match_none": {}} in _filters(clause), json.dumps(clause)

    def test_the_restriction_names_no_database(self, search_module, builder_kind, index_type):
        clause = _clause(search_module, builder_kind, [], index_type)
        assert _database_clauses(clause) == [], json.dumps(_database_clauses(clause))


@pytest.mark.unit
class TestBothIndexQueriesCarryTheRestriction:
    """Through the public entry points, which fetch the accessible list themselves. The aggregations
    are computed over the same ``query``, so the restriction scopes them as well."""

    @staticmethod
    def _manager():
        manager = MagicMock()
        manager.get_accessible_databases.return_value = list(_ACCESSIBLE)
        return manager

    def test_the_simple_search_queries(self, search_module):
        manager = self._manager()
        builder = search_module.SimpleSearchQueryBuilder(manager)
        asset_query, file_query = builder.build_simple_dual_index_queries(
            search_module.SimpleSearchRequestModel(query="pump"), _CLAIMS
        )
        manager.get_accessible_databases.assert_called_once_with(_CLAIMS, show_deleted=False)
        for query in (asset_query, file_query):
            assert _KEYWORD_RESTRICTION in _filters(query["query"]), json.dumps(query["query"])
            assert _analyzed_field_queries(query) == []

    def test_the_search_queries(self, search_module):
        manager = self._manager()
        builder = search_module.DualIndexQueryBuilder(manager)
        asset_query, file_query = builder.build_dual_index_queries(
            search_module.SearchRequestModel(query="pump", aggregations=True), _CLAIMS
        )
        manager.get_accessible_databases.assert_called_once_with(_CLAIMS, show_deleted=False)
        for query in (asset_query, file_query):
            assert "aggs" in query
            assert _KEYWORD_RESTRICTION in _filters(query["query"]), json.dumps(query["query"])
            assert _analyzed_field_queries(query) == []


@pytest.mark.unit
class TestUnchangedFilters:
    """Controls: these hold before and after the restriction changes form."""

    def test_the_simple_search_database_id_is_still_an_exact_term(self, search_module):
        for index_type in _INDEXES:
            clause = _clause(
                search_module, "simple", list(_ACCESSIBLE), index_type, databaseId="smoke-db"
            )
            assert {"term": {"str_databaseid.keyword": "smoke-db"}} in _filters(clause)

    def test_caller_filters_still_reach_the_query(self, search_module):
        caller_filter = {"query_string": {"query": 'str_assettype.keyword:"model"'}}
        for index_type in _INDEXES:
            clause = _clause(
                search_module, "complex", list(_ACCESSIBLE), index_type, filters=[caller_filter]
            )
            assert caller_filter in _filters(clause)


@pytest.mark.unit
class TestAnalyzedFieldDetector:
    """Controls for ``_analyzed_field_queries``: an absence assertion is only as good as the search
    behind it."""

    def test_it_finds_the_analyzed_field_at_any_depth(self):
        body = {"bool": {"filter": [{"query_string": {"query": 'str_databaseid:("smoke-db")'}}]}}
        assert _analyzed_field_queries(body) == ['str_databaseid:("smoke-db")']

    def test_it_ignores_the_keyword_subfield(self):
        body = {"bool": {"filter": [{"query_string": {"query": 'str_databaseid.keyword:"smoke-db"'}}]}}
        assert _analyzed_field_queries(body) == []
