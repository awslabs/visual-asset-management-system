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
