# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging reaches every readable record when the caller's rules are checked after the query.

A rule the index cannot decide (a character class in a name rule) leaves OpenSearch returning
candidates the per-hit check refuses. The search keeps fetching larger windows until the requested
page is full or the indexes are exhausted, within the SEARCH_FILL_* bounds; a search stopped by a bound
reports its total as a minimum. Driven end to end against the in-memory OpenSearch of
``opensearch_dsl_oracle``.
"""

import json

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    CLAIMS, ROLE, constraint, crit, load_search_module, policies,
)
from backend.tests.handlers.authz.test_search_counts_honour_permissions import (
    DB, TYPE_X_ONLY, _asset, _file, _install_rows, _manager,
)

# A name rule with a character class: the index cannot decide it, so every hit is checked after the query
REGEX_ONLY = [constraint("name-class", [crit("databaseId", "equals", DB), crit("assetName", "contains", "proj[A-Z]a")])]
READABLE_AT = (0, 50, 120, 200, 290)
READABLE_IDS = [f"a{n}" for n in READABLE_AT]


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_paging")


def _sparse_assets(count=300, readable_at=READABLE_AT):
    return [_asset(n, "typeX", ["t1"], name="projXa" if n in readable_at else f"other-{n}") for n in range(count)]


def _run(module, monkeypatch, fake, body, simple=False):
    manager = _manager(module, monkeypatch)
    processor = module.DualIndexResponseProcessor(manager)
    event = {"body": json.dumps(body)}
    if simple:
        response = module.handle_simple_post_request(event, fake, module.SimpleSearchQueryBuilder(manager),
                                                     processor, CLAIMS)
    else:
        response = module.handle_post_request(event, fake, module.DualIndexQueryBuilder(manager), processor, CLAIMS)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"])


def _browse(size, offset=0, entity_types=("asset",)):
    return {"aggregations": True, "entityTypes": list(entity_types), "from": offset, "size": size}


def _follow_pages(module, monkeypatch, assets, files, size, simple=False, entity_types=("asset",)):
    """Every page of ``size`` until an empty page, as a client follows them; returns the hit ids in order
    and the answers."""
    ids, answers, offset = [], [], 0
    while True:
        fake = FakeSearchManager(module, assets, files)
        body = ({"entityTypes": list(entity_types), "from": offset, "size": size} if simple
                else _browse(size, offset, entity_types))
        answer = _run(module, monkeypatch, fake, body, simple=simple)
        page = [h["_id"] for h in answer["hits"]["hits"]]
        answers.append(answer)
        if not page:
            return ids, answers
        ids.extend(page)
        offset += size
        assert offset < 100, "paging did not end"


@pytest.mark.unit
class TestPagingUnderRulesCheckedAfterTheQuery:
    def test_page_size_ten_reaches_all_five_readable_assets(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert [h["_id"] for h in result["hits"]["hits"]] == READABLE_IDS
        assert result["hits"]["total"] == {"value": 5, "relation": "eq"}

    @pytest.mark.parametrize("size", [2, 3, 10])
    def test_following_every_page_reaches_each_readable_asset_once_in_order(self, search_module, monkeypatch, size):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        with policies(REGEX_ONLY, (ROLE,)):
            ids, _ = _follow_pages(search_module, monkeypatch, assets, [], size)

        assert ids == READABLE_IDS

    def test_simple_search_pages_reach_every_readable_asset(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        with policies(REGEX_ONLY, (ROLE,)):
            ids, _ = _follow_pages(search_module, monkeypatch, assets, [], 2, simple=True)

        assert ids == READABLE_IDS

    def test_file_pages_reach_every_file_of_a_readable_asset(self, search_module, monkeypatch):
        assets = _sparse_assets()
        files = [_file(n, a) for n, a in enumerate(assets)]
        _install_rows(search_module, monkeypatch, assets)
        with policies(REGEX_ONLY, (ROLE,)):
            ids, _ = _follow_pages(search_module, monkeypatch, assets, files, 2, entity_types=("file",))

        assert ids == [f"f{n}" for n in READABLE_AT]

    def test_results_never_include_an_unreadable_document(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        with policies(REGEX_ONLY, (ROLE,)):
            _, answers = _follow_pages(search_module, monkeypatch, assets, [], 3)

        for answer in answers:
            for hit in answer["hits"]["hits"]:
                assert hit["_source"]["str_assetname"] == "projXa"

    def test_larger_windows_request_no_aggregations_and_keep_the_first_ones(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        sizes = [q[0]["size"] for q in fake.queries]
        assert sizes == [20, 80, 320]
        assert "aggs" in fake.queries[0][0]
        assert all("aggs" not in q[0] for q in fake.queries[1:])
        assert {b["key"]: b["doc_count"] for b in result["aggregations"]["str_assettype"]["buckets"]} == {"typeX": 5}


@pytest.mark.unit
class TestBounds:
    def test_a_role_the_index_decides_issues_one_query(self, search_module, monkeypatch):
        assets = [_asset(n, "typeX", ["t1"]) for n in range(300)] + [_asset(n, "typeY", []) for n in range(300, 350)]
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert len(fake.queries) == 1
        assert len(result["hits"]["hits"]) == 10
        assert result["hits"]["total"] == {"value": 300, "relation": "eq"}

    def test_the_window_bound_reports_a_lower_bound_total(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        monkeypatch.setattr(search_module, "SEARCH_FILL_MAX_WINDOWS", 2)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert len(fake.queries) == 2
        assert [h["_id"] for h in result["hits"]["hits"]] == ["a0", "a50"]
        assert result["hits"]["total"] == {"value": 2, "relation": "gte"}

    def test_the_time_bound_stops_further_windows(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        monkeypatch.setattr(search_module, "SEARCH_FILL_MAX_SECONDS", 0.0)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert len(fake.queries) == 1
        assert result["hits"]["total"] == {"value": 1, "relation": "gte"}

    def test_the_window_never_exceeds_the_result_window(self, search_module, monkeypatch):
        assets = _sparse_assets(count=400, readable_at=(0,))
        _install_rows(search_module, monkeypatch, assets)
        monkeypatch.setattr(search_module, "OPENSEARCH_MAX_RESULT_WINDOW", 100)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert [q[0]["size"] for q in fake.queries] == [20, 80, 100]
        assert result["hits"]["total"] == {"value": 1, "relation": "gte"}

    def test_a_refused_larger_window_answers_from_the_last_one(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        answer = fake.search_dual_index
        requested_windows = []

        def refuse_larger_windows(asset_query, file_query, entity_types):
            requested_windows.append(asset_query["size"])
            if asset_query["size"] > 20:
                raise search_module.VAMSGeneralErrorResponse("Error executing search query")
            return answer(asset_query, file_query, entity_types)

        fake.search_dual_index = refuse_larger_windows
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        # the refused window is the last one tried
        assert 80 in requested_windows and max(requested_windows) == 80
        assert len(requested_windows) <= 2
        assert [h["_id"] for h in result["hits"]["hits"]] == ["a0"]
        assert result["hits"]["total"]["relation"] == "gte"

    def test_a_fallback_search_grows_with_the_fallback_queries(self, search_module, monkeypatch):
        """When the filtered query is refused, the larger windows reuse the unfiltered fallback."""
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [], refuse_first=True)
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert [h["_id"] for h in result["hits"]["hits"]] == READABLE_IDS
        assert result["hits"]["total"] == {"value": 5, "relation": "eq"}
        assert result["aggregations"] == {}
        fallback = fake.queries[1][0]
        for later in fake.queries[2:]:
            assert later[0]["query"] == fallback["query"]

    def test_each_hit_is_decided_once_per_request(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        original = search_module.DualIndexResponseProcessor._hit_decision
        decided = []

        def counting(self, hit, claims):
            decided.append(hit["_id"])
            return original(self, hit, claims)

        monkeypatch.setattr(search_module.DualIndexResponseProcessor, "_hit_decision", counting)
        with policies(REGEX_ONLY, (ROLE,)):
            _run(search_module, monkeypatch, fake, _browse(10))

        assert len(decided) == len(set(decided)) == 300
