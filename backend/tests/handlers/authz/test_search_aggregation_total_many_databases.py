# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""aggregationTotal counts every matching record when the hits span more databases than the facet's
bucket limit (#770).

The database facet is a terms aggregation of at most 1,000 buckets; OpenSearch counts the documents of
every further value in ``sum_other_doc_count``. The fake below adds that cut to the in-memory search, as
OpenSearch answers a terms aggregation, and the searches run end to end through the handler.
"""

import json

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    ALLOW_ALL, CLAIMS, ROLE, constraint, crit, load_search_module, policies,
)
from backend.tests.handlers.authz.test_search_counts_honour_permissions import _asset, _file, _install_rows

DATABASES = [f"many-db-{n:04d}" for n in range(1005)]
ASSETS = [_asset(n, "typeX", ["t1"], name="projXa", database_id=db) for n, db in enumerate(DATABASES)]
# A name rule with a character class: the index cannot decide it, so the counts are lower bounds
NAME_CLASS = [constraint("name-class", [crit("databaseId", "contains", ".*"), crit("assetName", "contains", "proj[A-Z]a")])]


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_aggregation_total")


class _TermsSizeCut(FakeSearchManager):
    """The in-memory search with each terms aggregation cut to its ``size`` and the rest counted in
    ``sum_other_doc_count``, buckets ordered by count and then key, as OpenSearch answers."""

    def _answer(self, query, index_type):
        answer = super()._answer(query, index_type)
        for name, aggregation in (query.get("aggs") or {}).items():
            for sub_name, sub in (aggregation.get("aggs") or {}).items():
                result = answer["aggregations"][name][sub_name]
                buckets = sorted(result["buckets"], key=lambda b: (-b["doc_count"], str(b["key"])))
                size = sub["terms"].get("size", 10)
                result["buckets"] = buckets[:size]
                result["sum_other_doc_count"] = sum(b["doc_count"] for b in buckets[size:])
        return answer


def _search(module, monkeypatch, fake, entity_types=("asset",), size=10):
    manager = module.DatabaseAccessManager()
    monkeypatch.setattr(manager, "get_accessible_databases",
                        lambda claims_and_roles, show_deleted=False, **kw: list(DATABASES))
    processor = module.DualIndexResponseProcessor(manager)
    body = {"aggregations": True, "entityTypes": list(entity_types), "from": 0, "size": size}
    response = module.handle_post_request({"body": json.dumps(body)}, fake,
                                          module.DualIndexQueryBuilder(manager), processor, CLAIMS)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"])


@pytest.mark.unit
class TestTheTotalPastTheBucketLimit:
    def test_exact_permissions_count_every_record(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, ASSETS, []))

        assert len(result["aggregations"]["str_databaseid"]["buckets"]) == 1000
        assert result["hits"]["total"] == {"value": 1005, "relation": "eq"}
        assert result["aggregationTotal"] == 1005

    def test_both_indexes_count_every_record(self, search_module, monkeypatch):
        files = [_file(n, a) for n, a in enumerate(ASSETS)]
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, ASSETS, files),
                             ("asset", "file"))

        assert result["aggregationTotal"] == 2010

    def test_permissions_checked_per_hit_count_every_readable_record_once(self, search_module, monkeypatch):
        """Under a rule the index cannot decide, the facet counts are raised to the hits that passed; the
        buckets past the limit are counted in the total, each record once. The page is large enough for
        the search to check every candidate."""
        unreadable = [_asset(2000 + n, "typeX", ["t1"], name=f"other-{n}", database_id=DATABASES[n])
                      for n in range(20)]
        assets = ASSETS + unreadable
        _install_rows(search_module, monkeypatch, assets)
        with policies(NAME_CLASS, (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, assets, []), size=2000)

        buckets = result["aggregations"]["str_databaseid"]["buckets"]
        assert len(buckets) == 1000
        assert sum(b["doc_count"] for b in buckets) == 1000
        assert result["aggregationTotal"] == 1005

    def test_a_total_under_the_limit_is_unchanged(self, search_module, monkeypatch):
        few = ASSETS[:7]
        _install_rows(search_module, monkeypatch, few)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, _TermsSizeCut(search_module, few, []))

        assert result["aggregationTotal"] == 7


@pytest.mark.unit
class TestRaisedBucketsCountEachRecordOnce:
    def test_hits_of_values_opensearch_did_not_name_are_counted_in_the_rest(self, search_module):
        """A value OpenSearch counted only in sum_other_doc_count is not added as a bucket as well."""
        processor = search_module.DualIndexResponseProcessor
        hits = [{"_index_type": "asset", "_source": {"str_databaseid": "named"}},
                {"_index_type": "asset", "_source": {"str_databaseid": "unnamed"}},
                {"_index_type": "asset", "_source": {"str_databaseid": "unnamed"}}]
        aggregations = {"str_databaseid": {"buckets": [{"key": "named", "doc_count": 4}],
                                           "sum_other_doc_count": 1}}
        raised = processor._raise_buckets_to_passed_hits(aggregations, hits)["str_databaseid"]

        assert raised["buckets"] == [{"key": "named", "doc_count": 4}]
        assert raised["sum_other_doc_count"] == 2
        assert processor._compute_aggregation_total(None, {"str_databaseid": raised}) == 6

    def test_with_every_value_named_the_hits_add_buckets(self, search_module):
        processor = search_module.DualIndexResponseProcessor
        hits = [{"_index_type": "asset", "_source": {"str_databaseid": "only-in-hits"}}]
        aggregations = {"str_databaseid": {"buckets": [{"key": "named", "doc_count": 2}]}}
        raised = processor._raise_buckets_to_passed_hits(aggregations, hits)["str_databaseid"]

        assert raised["buckets"] == [{"key": "named", "doc_count": 2}, {"key": "only-in-hits", "doc_count": 1}]
        assert raised["sum_other_doc_count"] == 0
