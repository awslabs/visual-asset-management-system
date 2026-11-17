# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A slow first fetch still gets its page checked, and a page the time bound cut short is never an exact total (#1590).

Seen live: the first keyword search after idle hours took 10,452 ms for its first window. The fill bound was
SEARCH_FILL_MAX_SECONDS from the start of the search, so no time was left for the per-hit checks: the page
was cut to zero hits while the total still came from OpenSearch with relation ``eq`` (exact permission
filters), with no warning. Now the first window gets SEARCH_FIRST_WINDOW_CHECK_SHARE of the bound after it
answers (never past SEARCH_REQUEST_MAX_SECONDS from the start), and an answer whose checks the bound stopped
before the requested page was full reports ``gte`` and carries the ``truncated:time`` warning. Driven end to
end through both keyword handlers on a fake clock, against the in-memory OpenSearch of
``opensearch_dsl_oracle``.
"""

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import ROLE, load_search_module, policies
from backend.tests.handlers.authz.test_search_counts_honour_permissions import TYPE_X_ONLY, _asset, _install_rows
from backend.tests.handlers.authz.test_search_paging_reaches_readable_assets import (
    REGEX_ONLY, _browse, _run, _sparse_assets, _timed,
)

TIME_WARNING = "truncated:time"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_time_bound")


def _readable_assets(count):
    """Assets every candidate of which the caller may read: TYPE_X_ONLY is decided by the index (exact)."""
    return [_asset(n, "typeX", ["t1"]) for n in range(count)]


def _search(module, monkeypatch, assets, first_fetch_seconds, decision_seconds, size, simple=False):
    _install_rows(module, monkeypatch, assets)
    fake = FakeSearchManager(module, assets, [])
    clock = _timed(module, monkeypatch, fake, first_fetch_seconds, 0.0, decision_seconds)
    started = clock.now
    body = {"entityTypes": ["asset"], "from": 0, "size": size} if simple else _browse(size)
    with policies(TYPE_X_ONLY, (ROLE,)):
        result = _run(module, monkeypatch, fake, body, simple=simple)
    return result, clock.now - started, fake


@pytest.mark.unit
class TestASlowFirstFetchStillAnswersItsPage:
    @pytest.mark.parametrize("simple", [False, True])
    def test_a_first_fetch_past_the_bound_returns_the_readable_page(self, search_module, monkeypatch, simple):
        result, _elapsed, fake = _search(search_module, monkeypatch, _readable_assets(60), 10.452, 0.01, 50,
                                         simple=simple)

        assert len(fake.queries) == 1
        assert [h["_id"] for h in result["hits"]["hits"]] == [f"a{n}" for n in range(50)]
        assert result["hits"]["total"] == {"value": 60, "relation": "eq"}
        assert result["warnings"] == []

    def test_the_first_window_gets_half_the_bound_after_it_answers(self, search_module):
        started = 1000.0
        bound = search_module.SEARCH_FILL_MAX_SECONDS
        # A fast first fetch keeps the bound from the start.
        assert search_module._fill_deadline(started, started + 0.2) == started + bound
        # A first fetch past the bound gets half the bound after it.
        assert search_module._fill_deadline(started, started + 10.452) == pytest.approx(started + 10.452 + bound / 2)
        # Never past the request cap, which leaves 9 s of the API's 29 s for the rest of the request.
        cap = started + search_module.SEARCH_REQUEST_MAX_SECONDS
        assert search_module._fill_deadline(started, started + 18.0) == cap
        assert search_module.SEARCH_REQUEST_MAX_SECONDS <= 29 - 9


@pytest.mark.unit
class TestAPageCutShortIsNeverAnExactTotal:
    @pytest.mark.parametrize("simple", [False, True])
    def test_a_check_cut_short_reports_a_minimum_and_the_warning(self, search_module, monkeypatch, simple):
        """The first window answers at 18 s; the checks may run to the 20 s cap, 4 of them at 0.5 s each:
        a page of 50 holds 4 hits, so its total is a minimum and the answer says why."""
        result, elapsed, _fake = _search(search_module, monkeypatch, _readable_assets(60), 18.0, 0.5, 50,
                                         simple=simple)

        assert [h["_id"] for h in result["hits"]["hits"]] == ["a0", "a1", "a2", "a3"]
        assert result["hits"]["total"] == {"value": 60, "relation": "gte"}
        assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]
        assert result["warnings"][0]["message"] == search_module.SEARCH_TIME_BOUND_WARNING_MESSAGE
        # At most one check past the cap.
        assert elapsed <= search_module.SEARCH_REQUEST_MAX_SECONDS + 0.5

    def test_a_first_fetch_past_the_request_cap_answers_no_hits_as_a_minimum(self, search_module, monkeypatch):
        result, elapsed, _fake = _search(search_module, monkeypatch, _readable_assets(60), 25.0, 0.01, 50)

        assert result["hits"]["hits"] == []
        assert result["hits"]["total"] == {"value": 60, "relation": "gte"}
        assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]
        assert elapsed == pytest.approx(25.0)  # no check ran after the cap

    def test_a_stop_after_the_page_is_full_keeps_the_exact_total_and_no_warning(self, search_module, monkeypatch):
        """The checks stop at the bound with the requested page already full: the page is whole and the
        exact filters' total stands."""
        result, _elapsed, _fake = _search(search_module, monkeypatch, _readable_assets(300), 0.2, 0.6, 10)

        assert [h["_id"] for h in result["hits"]["hits"]] == [f"a{n}" for n in range(10)]
        assert result["hits"]["total"] == {"value": 300, "relation": "eq"}
        assert result["warnings"] == []


@pytest.mark.unit
class TestTheWarningNamesTheTimeBoundOnly:
    """Under rules checked after the query, a page that is not full because no larger window fits the time
    left carries the warning; one stopped by the per-index window cap does not (a minimum, as before)."""

    def test_a_window_the_time_left_cannot_afford_is_a_time_stop(self, search_module, monkeypatch):
        assets = _sparse_assets(count=2020, readable_at=(40, 120, 200, 280, 360))
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        _timed(search_module, monkeypatch, fake, 0.2, 0.0002, 0.015)
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert len(result["hits"]["hits"]) < 10
        assert result["hits"]["total"]["relation"] == "gte"
        assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]

    def test_the_window_cap_is_not_a_time_stop(self, search_module, monkeypatch):
        assets = _sparse_assets(count=400, readable_at=(0,))
        _install_rows(search_module, monkeypatch, assets)
        monkeypatch.setattr(search_module, "OPENSEARCH_MAX_RESULT_WINDOW", 100)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result = _run(search_module, monkeypatch, fake, _browse(10))

        assert [q[0]["size"] for q in fake.queries] == [20, 80, 100]
        assert result["hits"]["total"] == {"value": 1, "relation": "gte"}
        assert result["warnings"] == []
