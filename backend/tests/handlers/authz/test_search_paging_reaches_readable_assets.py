# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging reaches every readable record when the caller's rules are checked after the query.

A rule the index cannot decide (a character class in a name rule) leaves OpenSearch returning
candidates the per-hit check refuses. The search keeps fetching larger windows until the requested
page is full or the indexes are exhausted, within the SEARCH_FILL_* bounds; a search stopped by a bound
reports its total as a minimum. Driven end to end against the in-memory OpenSearch of
``opensearch_dsl_oracle``.
"""

import contextlib
import json

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    CLAIMS, ROLE, authz_module, constraint, crit, load_search_module, policies,
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


class _ScoredFake(FakeSearchManager):
    """The in-memory search with a distinct score per document, so the merge of the two indexes'
    answers decides the order: ``layout`` "assets_first" scores every asset above every file,
    "interleaved" puts file n just after asset n."""

    def __init__(self, module, assets, files, layout):
        super().__init__(module, assets, files)
        self._layout = layout

    def _score(self, doc_id):
        n = int(doc_id[1:])
        if self._layout == "assets_first":
            return (2.0 if doc_id[0] == "a" else 1.0) - n / 10000
        return 1.0 - (n + (0.5 if doc_id[0] == "f" else 0.0)) / 1000

    def _answer(self, query, index_type):
        sources = self.indexes[index_type]
        ranked = sorted(sources, key=lambda s: -self._score(s["_id"]))
        self.indexes[index_type] = ranked
        answer = super()._answer(query, index_type)
        self.indexes[index_type] = sources
        for hit in answer["hits"]["hits"]:
            hit["_score"] = self._score(hit["_id"])
        return answer


@pytest.mark.unit
class TestCombinedAssetAndFilePaging:
    """Pages of a search over both indexes list each readable record once, in the one-page order."""

    @pytest.mark.parametrize("layout", ["assets_first", "interleaved"])
    @pytest.mark.parametrize("size", [2, 3])
    def test_pages_list_each_readable_record_once_in_the_one_page_order(self, search_module, monkeypatch,
                                                                        layout, size):
        assets = _sparse_assets()
        files = [_file(n, a) for n, a in enumerate(assets)]
        _install_rows(search_module, monkeypatch, assets)
        with policies(REGEX_ONLY, (ROLE,)):
            one_page = _run(search_module, monkeypatch, _ScoredFake(search_module, assets, files, layout),
                            _browse(50, 0, ("asset", "file")))
            ids, offset = [], 0
            while True:
                answer = _run(search_module, monkeypatch, _ScoredFake(search_module, assets, files, layout),
                              _browse(size, offset, ("asset", "file")))
                page = [h["_id"] for h in answer["hits"]["hits"]]
                if not page:
                    break
                ids.extend(page)
                offset += size
                assert offset < 100, "paging did not end"

        expected = [h["_id"] for h in one_page["hits"]["hits"]]
        assert sorted(expected) == sorted(READABLE_IDS + [f"f{n}" for n in READABLE_AT])
        assert ids == expected

    def test_a_role_with_every_candidate_readable_pages_both_indexes_in_one_query(self, search_module,
                                                                                  monkeypatch):
        assets = [_asset(n, "typeX", ["t1"]) for n in range(60)]
        files = [_file(n, a) for n, a in enumerate(assets)]
        _install_rows(search_module, monkeypatch, assets)
        fake = _ScoredFake(search_module, assets, files, "interleaved")
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10, 10, ("asset", "file")))

        assert len(fake.queries) == 1
        assert [h["_id"] for h in result["hits"]["hits"]] == [
            "a5", "f5", "a6", "f6", "a7", "f7", "a8", "f8", "a9", "f9"]


class _FakeClock:
    """A monotonic clock the test advances: each fetch and each per-hit decision costs a set time."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def _timed(module, monkeypatch, fake, fetch_seconds, fetch_seconds_per_hit, decision_seconds):
    """Run every fetch and every per-hit decision on a fake clock; returns the clock."""
    clock = _FakeClock()
    monkeypatch.setattr(module, "time", clock)
    answer = fake.search_dual_index

    def timed_fetch(asset_query, file_query, entity_types):
        response = answer(asset_query, file_query, entity_types)
        clock.now += fetch_seconds + fetch_seconds_per_hit * len(response["hits"]["hits"])
        return response

    fake.search_dual_index = timed_fetch
    original = module.DualIndexResponseProcessor._hit_decision

    def timed_decision(self, hit, claims):
        clock.now += decision_seconds
        return original(self, hit, claims)

    monkeypatch.setattr(module.DualIndexResponseProcessor, "_hit_decision", timed_decision)
    return clock


@pytest.mark.unit
class TestTimeBudget:
    """The time bound covers the whole fill loop: the fetches and every per-hit check after them."""

    # 2,020 candidates, 5 readable, at 15 ms a decision: the case the bound has to hold for
    LARGE_READABLE_AT = (40, 120, 200, 280, 360)

    def _large(self, module, monkeypatch):
        assets = _sparse_assets(count=2020, readable_at=self.LARGE_READABLE_AT)
        _install_rows(module, monkeypatch, assets)
        return assets

    @pytest.mark.parametrize("size", [2, 10, 100])
    def test_a_request_ends_within_the_bound_when_each_check_is_slow(self, search_module, monkeypatch, size):
        assets = self._large(search_module, monkeypatch)
        fake = FakeSearchManager(search_module, assets, [])
        clock = _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.015)
        started = clock.now
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(size))

        # at most one decision past the bound
        assert clock.now - started <= search_module.SEARCH_FILL_MAX_SECONDS + 0.015
        ids = [h["_id"] for h in result["hits"]["hits"]]
        assert ids == [f"a{n}" for n in self.LARGE_READABLE_AT][: len(ids)]
        assert result["hits"]["total"]["relation"] == "gte"

    def test_a_window_is_not_fetched_when_its_checks_cannot_finish_in_time(self, search_module, monkeypatch):
        """The next window is planned with the per-hit check time measured so far, not the fetch time
        alone: at 15 ms a check a 1,280-hit window cannot be checked in 10 s, so it is not requested."""
        assets = self._large(search_module, monkeypatch)
        fake = FakeSearchManager(search_module, assets, [])
        _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.015)
        with policies(REGEX_ONLY, (ROLE,)):
            _run(search_module, monkeypatch, fake, _browse(10))

        sizes = [q[0]["size"] for q in fake.queries]
        assert 1280 not in sizes
        # every requested window could be checked inside the bound
        assert sum(s - prev for prev, s in zip([0] + sizes, sizes)) * 0.015 <= search_module.SEARCH_FILL_MAX_SECONDS

    def test_cheap_checks_still_reach_every_readable_asset(self, search_module, monkeypatch):
        """With the per-hit check at 1 ms (no audit write in line), 2,020 candidates fit the bound."""
        assets = self._large(search_module, monkeypatch)
        expected = [f"a{n}" for n in self.LARGE_READABLE_AT]
        for size in (2, 10):
            ids, offset = [], 0
            while True:
                fake = FakeSearchManager(search_module, assets, [])
                _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.001)
                with policies(REGEX_ONLY, (ROLE,)):
                    answer = _run(search_module, monkeypatch, fake, _browse(size, offset))
                page = [h["_id"] for h in answer["hits"]["hits"]]
                if not page:
                    break
                ids.extend(page)
                offset += size
            assert ids == expected, size

    # The live 2,020-asset cell: the readable assets ranked 301st, 771st, 1,034th, 1,755th and 1,782nd
    DEEP_READABLE_AT = (300, 770, 1033, 1754, 1781)

    @pytest.mark.parametrize("size", [2, 10])
    def test_small_pages_reach_records_ranked_near_the_end_within_the_bound(self, search_module, monkeypatch,
                                                                             size):
        """The number of windows does not end the search: windows grow until the index is exhausted, so
        a page of 2 or 10 reaches records ranked ~1,800 of 2,020, and every request ends within the bound."""
        assets = _sparse_assets(count=2020, readable_at=self.DEEP_READABLE_AT)
        _install_rows(search_module, monkeypatch, assets)
        ids, offset = [], 0
        while True:
            fake = FakeSearchManager(search_module, assets, [])
            clock = _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.001)
            started = clock.now
            with policies(REGEX_ONLY, (ROLE,)):
                answer = _run(search_module, monkeypatch, fake, _browse(size, offset))
            assert clock.now - started <= search_module.SEARCH_FILL_MAX_SECONDS
            assert all(q[0]["size"] <= search_module.OPENSEARCH_MAX_RESULT_WINDOW for q in fake.queries)
            page = [h["_id"] for h in answer["hits"]["hits"]]
            if not page:
                break
            ids.extend(page)
            offset += size
            assert offset < 100, "paging did not end"

        assert ids == [f"a{n}" for n in self.DEEP_READABLE_AT]

    def test_a_search_stopped_by_the_bound_reports_a_lower_bound_total(self, search_module, monkeypatch):
        """At 15 ms a check, 2,020 candidates cannot be checked in the bound: the page holds the readable
        records reached, in order, and the total is a minimum."""
        assets = _sparse_assets(count=2020, readable_at=self.DEEP_READABLE_AT)
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        clock = _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.015)
        started = clock.now
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(2))

        assert clock.now - started <= search_module.SEARCH_FILL_MAX_SECONDS + 0.015
        ids = [h["_id"] for h in result["hits"]["hits"]]
        assert ids == [f"a{n}" for n in self.DEEP_READABLE_AT][: len(ids)]
        assert result["hits"]["total"]["relation"] == "gte"

    def test_the_first_window_is_bounded_too(self, search_module, monkeypatch):
        """Even the first window's checks stop at the bound; the page is the checked prefix, in order."""
        assets = self._large(search_module, monkeypatch)
        fake = FakeSearchManager(search_module, assets, [])
        clock = _timed(search_module, monkeypatch, fake, 0.2, 0.0, 0.015)
        monkeypatch.setattr(search_module, "SEARCH_FILL_MAX_SECONDS", 1.0)
        started = clock.now
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(100))

        assert len(fake.queries) == 1
        assert clock.now - started <= 1.0 + 0.015
        assert [h["_id"] for h in result["hits"]["hits"]] == ["a40"]
        assert result["hits"]["total"] == {"value": 1, "relation": "gte"}

    def test_refused_hit_audit_entries_are_written_after_the_checks_not_one_by_one(self, search_module,
                                                                                   monkeypatch):
        """Every refused per-hit check still records its audit entry, collected while the search
        decides and written together when it ends."""
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        authz = authz_module  # search.py's CasbinEnforcer, which writes the per-hit audit entries
        state = {"deferring": False, "deferred": 0, "inline": 0, "flushes": 0}

        @contextlib.contextmanager
        def deferring():
            state["deferring"] = True
            try:
                yield
            finally:
                state["deferring"] = False
                state["flushes"] += 1

        def record(claims, authorized, custom_data=None):
            state["deferred" if state["deferring"] else "inline"] += 1

        monkeypatch.setattr(search_module, "deferred_authorization_audit", deferring)
        monkeypatch.setattr(authz, "log_authorization", record)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            _run(search_module, monkeypatch, fake, _browse(10))

        assert state["inline"] == 0
        assert state["deferred"] == 295  # 300 candidates, 5 readable, each refusal recorded once
        assert state["flushes"] == 1


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

    def test_windows_grow_until_the_index_is_exhausted(self, search_module, monkeypatch):
        """With time to spare, the count of windows does not stop the search: a page of 2 whose only
        readable asset is the last candidate grows its window until the index has no more candidates."""
        assets = _sparse_assets(count=2020, readable_at=(2019,))
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(2))

        assert [q[0]["size"] for q in fake.queries] == [4, 16, 64, 256, 1024, 4096]
        assert [h["_id"] for h in result["hits"]["hits"]] == ["a2019"]
        assert result["hits"]["total"] == {"value": 1, "relation": "eq"}

    def test_the_time_bound_stops_further_windows(self, search_module, monkeypatch):
        assets = _sparse_assets()
        _install_rows(search_module, monkeypatch, assets)
        monkeypatch.setattr(search_module, "SEARCH_FILL_MAX_SECONDS", 0.0)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        # the bound covers the per-hit checks too: with no time, no hit is checked or returned
        assert len(fake.queries) == 1
        assert result["hits"]["hits"] == []
        assert result["hits"]["total"] == {"value": 0, "relation": "gte"}

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
