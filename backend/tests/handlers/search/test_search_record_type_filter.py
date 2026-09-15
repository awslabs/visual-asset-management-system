# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The query builder drops a legacy `_rectype` filter but keeps a `str_rectype` one.

`str_rectype` is the live record-type field on every indexed document, and it
contains the legacy name as a substring. A skip written as a plain substring test
therefore discards a legitimate filter on the live field, silently returning the
unfiltered result set instead of an error.
"""

import importlib.util
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# search.py reads env vars and calls SSM at import time; stub boto3 and load it by
# file path, the same approach as test_searchable_fields.py.
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


@pytest.fixture
def search_module():
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
                "search_record_type_under_test", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _request(filters):
    return SimpleNamespace(
        filters=filters,
        query=None,
        metadataQuery=None,
        metadataSearchMode="both",
        includeMetadataInSearch=False,
        includeArchived=True,
        geoSearch=None,
    )


def _query_strings(clause):
    """Every query_string query the builder placed in the bool filter list."""
    return [
        item["query_string"]["query"]
        for item in clause.get("bool", {}).get("filter", [])
        if "query_string" in item
    ]


@pytest.mark.unit
class TestRecordTypeFilterPassThrough:
    def test_str_rectype_filter_reaches_the_query(self, search_module):
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        clause = builder._build_query_clause(
            _request([{"query_string": {"query": '(str_rectype:("file"))'}}]), ["db1"], "file"
        )
        assert '(str_rectype:("file"))' in _query_strings(clause)

    def test_unrelated_filter_reaches_the_query(self, search_module):
        """Control: proves the harness surfaces a kept filter.

        Without it the drop assertion below could pass because nothing at all is
        collected from the clause.
        """
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        clause = builder._build_query_clause(
            _request([{"query_string": {"query": '(str_fileext:("glb"))'}}]), ["db1"], "file"
        )
        assert '(str_fileext:("glb"))' in _query_strings(clause)

    def test_legacy_underscore_rectype_filter_is_dropped(self, search_module):
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        clause = builder._build_query_clause(
            _request([{"query_string": {"query": '(_rectype:("asset"))'}}]), ["db1"], "asset"
        )
        assert '(_rectype:("asset"))' not in _query_strings(clause)

    def test_core_field_sets_name_the_live_record_type_field(self, search_module):
        classifier = search_module.FieldClassifier
        for field_set, index_type in (
            (classifier.ASSET_CORE_FIELDS, "asset"),
            (classifier.FILE_CORE_FIELDS, "file"),
        ):
            rectype_names = {name for name in field_set if "rectype" in name}
            assert rectype_names == {"str_rectype"}, (
                f"{index_type} core fields name record type {rectype_names}"
            )
            assert classifier.is_core_field("str_rectype", index_type)
