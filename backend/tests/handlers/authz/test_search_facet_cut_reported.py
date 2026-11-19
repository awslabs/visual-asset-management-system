# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A facet cut at its bucket limit says so: each aggregation carries ``sum_other_doc_count`` (#1283).

A facet is a terms aggregation of at most 1,000 buckets per index; OpenSearch counts the records of
every further value in ``sum_other_doc_count``. The response model dropped that count, so no client
could tell a facet list was incomplete. Driven end to end through the handler against the in-memory
search that cuts terms aggregations as OpenSearch does (``_TermsSizeCut``).
"""

import json

import pytest

from backend.tests.handlers.authz.search_authz_harness import ALLOW_ALL, ROLE, load_search_module, policies
from backend.tests.handlers.authz.test_search_aggregation_total_many_databases import (
    ASSETS, NAME_CLASS, _TermsSizeCut, _search,
)
from backend.tests.handlers.authz.test_search_counts_honour_permissions import _asset, _file, _install_rows


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_facet_cut")


@pytest.mark.unit
class TestTheCutIsReported:
    def test_a_facet_past_the_limit_reports_the_records_left_out(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, ASSETS, []))

        databases = result["aggregations"]["str_databaseid"]
        assert len(databases["buckets"]) == 1000
        assert databases["sum_other_doc_count"] == 5
        assert result["aggregations"]["str_assettype"]["sum_other_doc_count"] == 0

    def test_a_facet_under_the_limit_reports_zero(self, search_module, monkeypatch):
        few = ASSETS[:7]
        _install_rows(search_module, monkeypatch, few)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, few, []))

        assert {name: agg["sum_other_doc_count"] for name, agg in result["aggregations"].items()} == {
            "str_assettype": 0, "str_databaseid": 0, "list_tags": 0}

    def test_both_indexes_report_their_records_left_out_together(self, search_module, monkeypatch):
        files = [_file(n, a) for n, a in enumerate(ASSETS)]
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, ASSETS, files),
                             ("asset", "file"))

        databases = result["aggregations"]["str_databaseid"]
        assert databases["sum_other_doc_count"] == 10
        assert sum(b["doc_count"] for b in databases["buckets"]) + 10 == 2010

    def test_buckets_raised_to_the_checked_hits_report_the_cut_too(self, search_module, monkeypatch):
        """Under a rule the index cannot decide, the buckets are raised to the hits that passed and cut
        at the limit; the records past the cut are reported."""
        unreadable = [_asset(2000 + n, "typeX", ["t1"], name=f"other-{n}", database_id=ASSETS[n]["str_databaseid"])
                      for n in range(20)]
        assets = ASSETS + unreadable
        _install_rows(search_module, monkeypatch, assets)
        with policies(NAME_CLASS, (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, assets, []), size=2000)

        databases = result["aggregations"]["str_databaseid"]
        assert len(databases["buckets"]) == 1000 and databases["sum_other_doc_count"] == 5


@pytest.mark.unit
class TestTheModel:
    def test_the_count_is_carried_and_other_keys_are_still_ignored(self, search_module):
        model = search_module.AggregationModel.parse_obj(
            {"buckets": [{"key": "a", "doc_count": 2}], "sum_other_doc_count": 3,
             "doc_count_error_upper_bound": 0, "unexpected": 1})
        assert model.sum_other_doc_count == 3
        assert "unexpected" not in model.dict()

    def test_an_aggregation_without_the_count_reports_none(self, search_module):
        model = search_module.AggregationModel.parse_obj({"buckets": [{"key": "a", "doc_count": 2}]})
        assert model.sum_other_doc_count is None

    def test_the_response_body_carries_it(self, search_module):
        response = search_module.SearchResponseModel.parse_obj({
            "took": 1, "timed_out": False, "_shards": {"total": 1},
            "hits": {"hits": [], "total": {"value": 0, "relation": "eq"}},
            "aggregations": {"str_databaseid": {"buckets": [], "sum_other_doc_count": 4}},
        })
        body = json.loads(json.dumps(response.dict(by_alias=True)))
        assert body["aggregations"]["str_databaseid"]["sum_other_doc_count"] == 4
