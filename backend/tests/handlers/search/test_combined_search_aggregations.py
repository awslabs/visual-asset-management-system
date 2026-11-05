# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1027: a combined asset+file search must report both indexes' facet buckets.

Each index answers its facets as a ``filter`` aggregation wrapping a ``terms`` aggregation
(``DualIndexQueryBuilder._build_aggregations``), so the buckets sit one level down, under
``filtered_<field>``. The cross-index merge in ``search_dual_index`` must combine the buckets
at that level for the facets both indexes carry (``str_databaseid``, ``list_tags``), and
``aggregationTotal`` must then count assets and files alike.
"""

import copy
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
    """The real search module, loaded by file path with boto3 stubbed (same approach as
    ``test_simple_search_paging_and_merge.py``)."""
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
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_combined_aggregations", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _facet(nested_name, buckets):
    """One facet as OpenSearch answers the filter-wrapped terms aggregation."""
    return {
        "doc_count": sum(count for _, count in buckets),
        nested_name: {
            "doc_count_error_upper_bound": 0,
            "sum_other_doc_count": 0,
            "buckets": [{"key": key, "doc_count": count} for key, count in buckets],
        },
    }


_ASSET_AGGREGATIONS = {
    "str_assettype": _facet("filtered_assettype", [(".glb", 3), (".obj", 1)]),
    "str_databaseid": _facet("filtered_databaseid", [("db1", 3), ("db2", 1)]),
    "list_tags": _facet("filtered_tags", [("pump", 2), ("valve", 1)]),
}

_FILE_AGGREGATIONS = {
    "str_fileext": _facet("filtered_fileext", [(".glb", 4), (".png", 2)]),
    "str_databaseid": _facet("filtered_databaseid", [("db1", 4), ("db3", 2)]),
    "str_assetid": _facet("filtered_assetid", [("a1", 5), ("a2", 1)]),
    "list_tags": _facet("filtered_tags", [("pump", 3), ("gasket", 1)]),
}


def _response(aggregations):
    return {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {"hits": [], "total": {"value": 0, "relation": "eq"}},
        "aggregations": aggregations,
    }


def _manager(module):
    manager = module.DualIndexSearchManager()
    manager.client = MagicMock()
    manager.asset_index = "vams-asset"
    manager.file_index = "vams-file"

    def _search(body=None, index=None):
        source = _ASSET_AGGREGATIONS if index == "vams-asset" else _FILE_AGGREGATIONS
        return _response(copy.deepcopy(source))

    manager.client.search.side_effect = _search
    return manager


def _counts(buckets):
    return {bucket["key"]: bucket["doc_count"] for bucket in buckets}


def _exact_filters(module):
    """The permission filters of a caller every asset is readable to, as the query builders
    hand them to the processor (``apply_search_authz_filters``). Without filters the processor
    reports no OpenSearch counts at all, so the facet tests pass the filters the handler would."""
    unrestricted = module.SearchAuthzFilter(upper=module.MATCH_ALL, lower=module.MATCH_ALL, exact=True)
    return {module.ASSET_INDEX: unrestricted, module.FILE_INDEX: unrestricted}


def _inexact_filters(module):
    """Filters whose lower bound is narrower than the upper one: aggregations are computed
    under the lower bound and a per-index readable count is added to each query."""
    restricted = module.SearchAuthzFilter(
        upper=module.MATCH_ALL, lower={"term": {"str_databaseid.keyword": "db1"}}, exact=False
    )
    return {module.ASSET_INDEX: restricted, module.FILE_INDEX: restricted}


_USE_EXACT = object()


def _process(module, opensearch_response, entity_types, authz_filters=_USE_EXACT):
    processor = module.DualIndexResponseProcessor(MagicMock())
    request = module.SearchRequestModel(entityTypes=entity_types, aggregations=True)
    if authz_filters is _USE_EXACT:
        authz_filters = _exact_filters(module)
    response = processor.process_dual_search_response(
        opensearch_response, request, {"tokens": ["mock_token"], "roles": []},
        authz_filters=authz_filters,
    )
    return response.dict(by_alias=True)


@pytest.mark.unit
class TestCombinedSearchMergesNestedBuckets:
    def _combined(self, module):
        return _manager(module).search_dual_index({}, {}, ["asset", "file"])["aggregations"]

    def test_shared_database_facet_sums_both_indexes(self, search_module):
        aggregations = self._combined(search_module)
        buckets = aggregations["str_databaseid"]["filtered_databaseid"]["buckets"]
        assert _counts(buckets) == {"db1": 7, "db2": 1, "db3": 2}

    def test_shared_tag_facet_sums_both_indexes(self, search_module):
        aggregations = self._combined(search_module)
        buckets = aggregations["list_tags"]["filtered_tags"]["buckets"]
        assert _counts(buckets) == {"pump": 5, "valve": 1, "gasket": 1}

    def test_merged_buckets_are_ordered_by_count(self, search_module):
        aggregations = self._combined(search_module)
        buckets = aggregations["str_databaseid"]["filtered_databaseid"]["buckets"]
        assert [bucket["key"] for bucket in buckets] == ["db1", "db3", "db2"]

    def test_wrapper_document_count_sums_both_indexes(self, search_module):
        aggregations = self._combined(search_module)
        assert aggregations["str_databaseid"]["doc_count"] == 10

    def test_index_specific_facets_are_kept(self, search_module):
        aggregations = self._combined(search_module)
        assert _counts(aggregations["str_assettype"]["filtered_assettype"]["buckets"]) == {
            ".glb": 3, ".obj": 1,
        }
        assert _counts(aggregations["str_fileext"]["filtered_fileext"]["buckets"]) == {
            ".glb": 4, ".png": 2,
        }
        assert _counts(aggregations["str_assetid"]["filtered_assetid"]["buckets"]) == {
            "a1": 5, "a2": 1,
        }

    def test_no_empty_bucket_list_is_written_on_the_wrapper(self, search_module):
        aggregations = self._combined(search_module)
        assert "buckets" not in aggregations["str_databaseid"]
        assert "buckets" not in aggregations["list_tags"]


@pytest.mark.unit
class TestCombinedSearchResponse:
    """The handler's response: lifted facet buckets and aggregationTotal."""

    def _search(self, module, entity_types):
        raw = _manager(module).search_dual_index({}, {}, entity_types)
        return _process(module, raw, entity_types)

    def test_combined_facets_carry_both_indexes(self, search_module):
        response = self._search(search_module, ["asset", "file"])
        assert _counts(response["aggregations"]["str_databaseid"]["buckets"]) == {
            "db1": 7, "db2": 1, "db3": 2,
        }
        assert _counts(response["aggregations"]["list_tags"]["buckets"]) == {
            "pump": 5, "valve": 1, "gasket": 1,
        }

    def test_combined_aggregation_total_counts_assets_and_files(self, search_module):
        response = self._search(search_module, ["asset", "file"])
        assert response["aggregationTotal"] == 4 + 6

    def test_asset_only_search_is_unchanged(self, search_module):
        response = self._search(search_module, ["asset"])
        assert _counts(response["aggregations"]["str_databaseid"]["buckets"]) == {"db1": 3, "db2": 1}
        assert "str_fileext" not in response["aggregations"]
        assert response["aggregationTotal"] == 4

    def test_file_only_search_is_unchanged(self, search_module):
        response = self._search(search_module, ["file"])
        assert _counts(response["aggregations"]["str_databaseid"]["buckets"]) == {"db1": 4, "db3": 2}
        assert "str_assettype" not in response["aggregations"]
        assert response["aggregationTotal"] == 6


@pytest.mark.unit
class TestCombinedSearchFollowsPermissionFilters:
    """The combined merge sits under the permission-filter rules for totals and facet counts:
    no filters means no OpenSearch counts, and inexact filters keep their readable-count
    aggregations per index and out of the response."""

    def test_without_filters_combined_search_reports_no_counts(self, search_module):
        raw = _manager(search_module).search_dual_index({}, {}, ["asset", "file"])
        response = _process(search_module, raw, ["asset", "file"], authz_filters=None)
        assert response["aggregations"] == {}
        assert response["aggregationTotal"] is None

    def _inexact_manager(self, module):
        manager = _manager(module)

        def _search(body=None, index=None):
            if index == "vams-asset":
                aggregations = copy.deepcopy(_ASSET_AGGREGATIONS)
                aggregations[module.AUTHZ_LOWER_COUNT_AGGREGATION.format(index_type="asset")] = {"doc_count": 3}
            else:
                aggregations = copy.deepcopy(_FILE_AGGREGATIONS)
                aggregations[module.AUTHZ_LOWER_COUNT_AGGREGATION.format(index_type="file")] = {"doc_count": 4}
            return _response(aggregations)

        manager.client.search.side_effect = _search
        return manager

    def test_inexact_filters_keep_merged_buckets(self, search_module):
        raw = self._inexact_manager(search_module).search_dual_index({}, {}, ["asset", "file"])
        response = _process(search_module, raw, ["asset", "file"], authz_filters=_inexact_filters(search_module))
        assert _counts(response["aggregations"]["str_databaseid"]["buckets"]) == {
            "db1": 7, "db2": 1, "db3": 2,
        }

    def test_readable_count_aggregations_stay_per_index_and_are_not_returned(self, search_module):
        raw = self._inexact_manager(search_module).search_dual_index({}, {}, ["asset", "file"])
        names = {
            search_module.AUTHZ_LOWER_COUNT_AGGREGATION.format(index_type=t) for t in ("asset", "file")
        }
        assert names <= set(raw["aggregations"])
        response = _process(search_module, raw, ["asset", "file"], authz_filters=_inexact_filters(search_module))
        assert not names & set(response["aggregations"])
