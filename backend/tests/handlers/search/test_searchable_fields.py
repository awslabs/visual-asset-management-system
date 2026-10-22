# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests: identifier fields (assetId, databaseId, bucket fields) must
be included in the general (free-text) search field lists for both the complex
(`/search`) and simple (`/search/simple`) endpoints.

Previously a general keyword search could not match str_assetid / str_databaseid
because those fields were omitted from the searchable-core-field lists, even
though they are indexed and returned in _source.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# search.py reads env vars and calls SSM at import time. Set env and stub boto3
# so the module loads without AWS access. The mock `handlers`/`common` packages
# registered by the root conftest shadow the real ones, so we load search.py
# directly from its file path (same approach as the fileIndexer tests).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

# Identifier fields that must be free-text searchable on both indexes.
_EXPECTED_IDENTIFIER_FIELDS = {
    "str_assetid", "str_databaseid", "str_bucketid", "str_bucketname", "str_bucketprefix"
}


@pytest.fixture
def search_module():
    """Load the real search module by file path with boto3 stubbed.

    Dependency submodules (common.*, handlers.auth/authz, models.*) are wired
    into sys.modules by the root conftest autouse fixture; handlers.auth/authz
    are registered there as empty MockModules, so we provide minimal stubs for
    the symbols search.py imports at module load.
    """
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

    # The shared mock common.dynamodb lacks validate_pagination_info; provide a
    # stub module carrying the symbols search.py imports at module load.
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


@pytest.mark.unit
class TestSearchableIdentifierFields:
    def test_complex_search_asset_fields_include_identifiers(self, search_module):
        fields = set(search_module.FieldClassifier.get_searchable_core_fields("asset"))
        assert _EXPECTED_IDENTIFIER_FIELDS.issubset(fields)

    def test_complex_search_file_fields_include_identifiers(self, search_module):
        fields = set(search_module.FieldClassifier.get_searchable_core_fields("file"))
        assert _EXPECTED_IDENTIFIER_FIELDS.issubset(fields)

    def test_simple_search_asset_fields_include_identifiers(self, search_module):
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        fields = set(builder._get_simple_searchable_fields("asset"))
        assert _EXPECTED_IDENTIFIER_FIELDS.issubset(fields)

    def test_simple_search_file_fields_include_identifiers(self, search_module):
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        fields = set(builder._get_simple_searchable_fields("file"))
        assert _EXPECTED_IDENTIFIER_FIELDS.issubset(fields)

    def test_general_search_query_targets_identifier_fields(self, search_module):
        # The general text query must list the identifier fields in its
        # query_string "fields" so a free-text term can match them.
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        clause = builder._build_general_search_query("my-id", include_metadata=False, index_type="asset")
        should = clause["bool"]["should"]
        text_fields = set(should[0]["query_string"]["fields"])
        assert _EXPECTED_IDENTIFIER_FIELDS.issubset(text_fields)


def _query_builder(search_module):
    return search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())


def _search_request(search_module, **fields):
    return search_module.SearchRequestModel(**fields)


@pytest.mark.unit
class TestMetadataSearchModes:
    """Multi-term metadata queries in key and value mode, and field:value parsing in both mode."""

    def test_key_mode_and_query_requires_every_key(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query(
            "MD_str_a AND MD_str_b", "key", "asset"
        )
        assert clause == {
            "bool": {"must": [{"exists": {"field": "MD_.a"}}, {"exists": {"field": "MD_.b"}}]}
        }

    def test_value_mode_or_query_matches_any_value(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query("x OR y", "value", "asset")
        should = clause["bool"]["should"]
        assert clause["bool"]["minimum_should_match"] == 1
        assert [entry["query_string"]["query"] for entry in should] == ["*x*", "*y*"]
        assert all(entry["query_string"]["fields"] == ["MD_._value"] for entry in should)

    def test_value_mode_term_with_colon_is_a_value(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query("12:30", "value", "asset")
        assert clause["query_string"]["fields"] == ["MD_._value"]
        assert clause["query_string"]["query"] == "*12\\:30*"

    def test_both_mode_still_parses_field_value_pairs(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query(
            "MD_str_a:1 AND MD_str_b:2", "both", "asset"
        )
        queries = [entry["query_string"]["query"] for entry in clause["bool"]["must"]]
        assert queries == ['MD_.a:"1"', 'MD_.b:"2"']

    def test_query_with_no_term_is_rejected_with_its_message(self, search_module):
        builder = _query_builder(search_module)
        request = _search_request(search_module, metadataQuery="MD_str_ AND MD_str_", metadataSearchMode="key")
        with pytest.raises(search_module.VAMSGeneralErrorResponse) as raised:
            builder._build_index_query(request, ["db1"], "asset")
        assert "metadataQuery" in str(raised.value)

    def test_field_name_with_a_space_is_escaped_in_the_field_only(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query(
            "Project Code:A1 B2", "both", "asset"
        )
        assert clause["query_string"]["query"] == 'MD_.Project\\ Code:"A1 B2"'

    def test_field_name_tab_is_escaped(self, search_module):
        clause = _query_builder(search_module)._build_metadata_search_query(
            "Project\tCode:A1", "both", "asset"
        )
        assert clause["query_string"]["query"] == 'MD_.Project\\\tCode:"A1"'


def _aggregation_filters(query):
    return [aggregation["filter"] for aggregation in query["aggs"].values()]


@pytest.mark.unit
class TestAggregationArchivedScope:
    """Facet aggregations cover the same archived scope as the hits."""

    @pytest.mark.parametrize("index_type", ["asset", "file"])
    def test_include_archived_aggregations_do_not_exclude_archived(self, search_module, index_type):
        request = _search_request(search_module, includeArchived=True, aggregations=True)
        query = _query_builder(search_module)._build_index_query(request, ["db1"], index_type)
        filters = _aggregation_filters(query)
        assert filters
        assert all("bool_archived" not in str(entry) for entry in filters)

    @pytest.mark.parametrize("index_type", ["asset", "file"])
    def test_default_aggregations_exclude_archived(self, search_module, index_type):
        request = _search_request(search_module, includeArchived=False, aggregations=True)
        query = _query_builder(search_module)._build_index_query(request, ["db1"], index_type)
        filters = _aggregation_filters(query)
        assert filters
        assert all(
            entry == {"bool": {"must_not": [{"term": {"bool_archived": True}}]}} for entry in filters
        )
