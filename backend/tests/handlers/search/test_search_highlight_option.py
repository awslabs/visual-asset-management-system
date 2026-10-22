# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`includeHighlights` decides whether the index query asks OpenSearch for highlights.

`false` leaves the `highlight` block out of the query, so hits come back without a
`highlight` field. `explainResults` keeps it, because the match explanations are
built from the highlights.
"""

import importlib.util
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# search.py reads env vars and calls SSM at import time; stub boto3 and load it by
# file path, the same approach as test_search_record_type_filter.py.
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

    # Every name search.py imports from common.dynamodb; the query builder reads no table.
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_highlight_option_under_test", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _request(include_highlights, explain_results):
    return SimpleNamespace(
        from_=0,
        size=10,
        sort=None,
        aggregations=False,
        query="pump",
        metadataQuery=None,
        includeHighlights=include_highlights,
        explainResults=explain_results,
    )


def _highlight_config(search_module):
    """The asset index's highlight block, as the real builder produces it."""
    builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
    return builder._build_highlight_config("asset")


def _index_query(search_module, request):
    builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
    with patch.object(builder, "_build_query_clause", return_value={"match_all": {}}), patch.object(
        builder, "_build_sort_config", return_value=[]
    ):
        return builder._build_index_query(request, ["db1"], "asset")


@pytest.mark.unit
class TestIncludeHighlights:
    def test_default_request_asks_for_highlights(self, search_module):
        query = _index_query(search_module, _request(True, False))
        # The block is the builder's real highlight config, not an empty placeholder.
        assert query["highlight"] == _highlight_config(search_module)
        assert "str_*" in query["highlight"]["fields"]

    def test_false_leaves_highlights_out(self, search_module):
        query = _index_query(search_module, _request(False, False))
        assert "highlight" not in query
        # Control: the rest of the query is still built.
        assert query["query"] == {"match_all": {}}
        assert query["from"] == 0 and query["size"] > 0

    def test_explain_results_keeps_highlights_when_false(self, search_module):
        query = _index_query(search_module, _request(False, True))
        assert query["highlight"] == _highlight_config(search_module)

    def test_unset_keeps_highlights(self, search_module):
        query = _index_query(search_module, _request(None, False))
        assert query["highlight"] == _highlight_config(search_module)
