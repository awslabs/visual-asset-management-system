# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A slow first keyword search after idle time still answers its page while the API has time (#1763).

Seen live on OpenSearch Serverless in a VPC: the first query after about 30 minutes idle took 14-28 s. The
checks were capped at SEARCH_REQUEST_MAX_SECONDS (20 s) after the search started, whatever time the API still
had, so a first fetch of 15-20 s left its page partly unchecked and one past 20 s returned an empty page, while
the next, warm search answered at once. The cap is now the API deadline: API_GATEWAY_TIMEOUT_FLOOR_SECONDS
after API Gateway received the request (``requestContext.requestTimeEpoch``, so a cold start counts), less
SEARCH_RESPONSE_RESERVE_SECONDS for the work after the checks. Without a usable receipt time the checks stop
SEARCH_REQUEST_MAX_SECONDS after the handler started, as before. The larger windows and the checks past the
requested page keep the fill bound, and a hit that was not checked is never returned.

Driven through both keyword handlers against the in-memory OpenSearch of ``opensearch_dsl_oracle`` on the
harness's fake clock, whose wall clock (``FakeClock.time``) sets when the API received each request.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    CLAIMS, ROLE, install_fake_clock, load_search_module, policies,
)
from backend.tests.handlers.authz.test_search_counts_honour_permissions import (
    TYPE_X_ONLY, _asset, _install_rows, _manager,
)
from backend.tests.handlers.authz.test_search_paging_reaches_readable_assets import (
    REGEX_ONLY, _browse, _sparse_assets,
)

TIME_WARNING = "truncated:time"
COLD_START = 2.0  # seconds between API Gateway receiving the request and the handler starting


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_api_budget")


@pytest.fixture(autouse=True)
def fake_clock(search_module, monkeypatch):
    return install_fake_clock(search_module, monkeypatch)


def _cutoff(module):
    """Seconds after API Gateway received a request at which its checks stop."""
    return module.API_GATEWAY_TIMEOUT_FLOOR_SECONDS - module.SEARCH_RESPONSE_RESERVE_SECONDS


def _readable_assets(count):
    """Assets the caller may read, every one: TYPE_X_ONLY is decided by the index (exact counts)."""
    return [_asset(n, "typeX", ["t1"]) for n in range(count)]


def _event(clock, body, waited, received=True):
    """A search request API Gateway received ``waited`` seconds ago, or one without a receipt time."""
    event = {"body": json.dumps(body)}
    if received:
        event["requestContext"] = {"requestTimeEpoch": clock.epoch_ms(seconds_ago=waited)}
    return event


def _handle(module, monkeypatch, fake, event, simple):
    manager = _manager(module, monkeypatch)
    processor = module.DualIndexResponseProcessor(manager)
    if simple:
        response = module.handle_simple_post_request(event, fake, module.SimpleSearchQueryBuilder(manager),
                                                     processor, CLAIMS)
    else:
        response = module.handle_post_request(event, fake, module.DualIndexQueryBuilder(manager), processor, CLAIMS)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"])


def _search(module, monkeypatch, clock, assets, answered_at, decision_seconds, size, *, waited=COLD_START,
            received=True, constraints=TYPE_X_ONLY, simple=False):
    """One search whose handler starts ``waited`` s after API Gateway received it and whose first fetch
    answers ``answered_at`` s after that receipt. Returns the answer and the seconds from the receipt to the
    end of the search."""
    _install_rows(module, monkeypatch, assets)
    fake = FakeSearchManager(module, assets, [])
    clock.costs(answered_at - waited, 0.0, decision_seconds)
    body = {"entityTypes": ["asset"], "from": 0, "size": size} if simple else _browse(size)
    event = _event(clock, body, waited, received)
    receipt = clock.now - waited
    with policies(constraints, (ROLE,)):
        result = _handle(module, monkeypatch, fake, event, simple)
    return result, clock.now - receipt


def _ids(result):
    return [h["_id"] for h in result["hits"]["hits"]]


@pytest.mark.unit
class TestTheFirstPageIsCheckedWithinTheApiBudget:
    @pytest.mark.parametrize("answered_at,decision_seconds", [(14.0, 0.4), (19.0, 0.4), (26.0, 0.04)])
    @pytest.mark.parametrize("simple", [False, True])
    def test_a_slow_first_fetch_returns_its_checked_page(self, search_module, monkeypatch, fake_clock,
                                                         answered_at, decision_seconds, simple):
        """The first fetch answers 14, 19 or 26 s after the API received the request; the page's checks
        take 4 s, 4 s and 0.4 s. Each page is checked in full before the API's time runs out."""
        result, elapsed = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), answered_at,
                                  decision_seconds, 10, simple=simple)

        assert _ids(result) == [f"a{n}" for n in range(10)]
        assert result["hits"]["total"] == {"value": 60, "relation": "eq"}
        assert result["warnings"] == []
        # No check started after the API deadline
        assert elapsed <= _cutoff(search_module) + decision_seconds

    @pytest.mark.parametrize("simple", [False, True])
    def test_a_first_fetch_after_the_api_budget_is_spent_checks_nothing(self, search_module, monkeypatch,
                                                                        fake_clock, simple):
        result, elapsed = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 27.0, 0.04, 10,
                                  simple=simple)

        assert result["hits"]["hits"] == []
        assert result["hits"]["total"] == {"value": 60, "relation": "gte"}
        assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]
        assert elapsed == pytest.approx(27.0)  # no check ran after the fetch


@pytest.mark.unit
class TestTheBudgetRunsFromWhenTheApiReceivedTheRequest:
    def test_a_cold_start_counts_against_the_budget(self, search_module, monkeypatch, fake_clock):
        """The same search, its first fetch answering 23 s after the handler started: with no wait before
        the handler, the API still has 3.5 s and the page is checked; after a 5 s cold start it has none."""
        warm, _ = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 23.0, 0.04, 10, waited=0.0)
        cold, elapsed = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 28.0, 0.04, 10,
                                waited=5.0)

        assert _ids(warm) == [f"a{n}" for n in range(10)]
        assert warm["warnings"] == []
        assert cold["hits"]["hits"] == []
        assert cold["hits"]["total"]["relation"] == "gte"
        assert [w["code"] for w in cold["warnings"]] == [TIME_WARNING]
        assert elapsed == pytest.approx(28.0)

    def test_without_a_receipt_time_the_checks_stop_20_s_after_the_handler_started(self, search_module,
                                                                                   monkeypatch, fake_clock):
        """The first fetch answers 19 s after the handler started and each check takes 0.4 s. Without a
        receipt time the checks stop at 20 s, as they did before #1763: three hits. With one, at no wait
        before the handler, the API's own time is used and the page is whole."""
        unknown, elapsed = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 19.0, 0.4, 10,
                                   waited=0.0, received=False)
        known, _ = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 19.0, 0.4, 10,
                           waited=0.0)

        assert _ids(unknown) == ["a0", "a1", "a2"]
        assert unknown["hits"]["total"] == {"value": 60, "relation": "gte"}
        assert [w["code"] for w in unknown["warnings"]] == [TIME_WARNING]
        assert elapsed <= search_module.SEARCH_REQUEST_MAX_SECONDS + 0.4
        assert _ids(known) == [f"a{n}" for n in range(10)]

    def test_the_deadline_runs_from_the_receipt_time(self, search_module, fake_clock):
        event = {"requestContext": {"requestTimeEpoch": fake_clock.epoch_ms(seconds_ago=3.0)}}

        assert search_module._api_check_deadline(event) == pytest.approx(
            fake_clock.now - 3.0 + _cutoff(search_module), abs=1e-3)

    def test_a_receipt_time_slightly_ahead_of_this_clock_is_read_as_now(self, search_module, fake_clock):
        event = {"requestContext": {"requestTimeEpoch": fake_clock.epoch_ms(seconds_ago=-0.5)}}

        assert search_module._api_check_deadline(event) == pytest.approx(fake_clock.now + _cutoff(search_module))

    @pytest.mark.parametrize("make_event", [
        lambda clock: {},
        lambda clock: {"requestContext": None},
        lambda clock: {"requestContext": ["not", "a", "dict"]},
        lambda clock: {"requestContext": {}},
        lambda clock: {"requestContext": {"requestTimeEpoch": None}},
        lambda clock: {"requestContext": {"requestTimeEpoch": str(clock.epoch_ms())}},
        lambda clock: {"requestContext": {"requestTimeEpoch": True}},
        lambda clock: {"requestContext": {"requestTimeEpoch": float("nan")}},
        lambda clock: {"requestContext": {"requestTimeEpoch": float("inf")}},
        lambda clock: {"requestContext": {"requestTimeEpoch": clock.epoch_ms(seconds_ago=-5.0)}},  # ahead
        lambda clock: {"requestContext": {"requestTimeEpoch": clock.epoch_ms(seconds_ago=40.0)}},  # stale
        lambda clock: "not an event",
    ], ids=["no-context", "context-none", "context-list", "no-time", "time-none", "time-string", "time-bool",
            "time-nan", "time-inf", "time-ahead", "time-stale", "event-string"])
    def test_without_a_usable_receipt_time_the_deadline_runs_from_now(self, search_module, fake_clock,
                                                                      make_event):
        assert search_module._api_check_deadline(make_event(fake_clock)) == pytest.approx(
            fake_clock.now + search_module.SEARCH_REQUEST_MAX_SECONDS)


@pytest.mark.unit
class TestTheOtherChecksKeepTheFillBound:
    def test_after_a_late_first_window_fills_its_page_the_rest_stop_at_the_fill_bound(self, search_module,
                                                                                      monkeypatch, fake_clock):
        """The first fetch answers 8 s in and the page's 10 checks end at 11 s. The checks of the rest of
        the window are past the requested page, so they keep the 10 s fill bound and none runs."""
        result, elapsed = _search(search_module, monkeypatch, fake_clock, _readable_assets(60), 8.0, 0.3, 10,
                                  waited=0.0)

        assert _ids(result) == [f"a{n}" for n in range(10)]
        assert result["hits"]["total"] == {"value": 60, "relation": "eq"}
        assert result["warnings"] == []
        assert elapsed == pytest.approx(11.0)

    def test_larger_windows_stop_at_the_fill_bound_while_the_api_has_time(self, search_module, monkeypatch,
                                                                          fake_clock):
        """A role checked after the query, with 15 ms a check: the windows grow and stop at the 10 s fill
        bound although the API would wait 26.5 s."""
        assets = _sparse_assets(count=2020, readable_at=(40, 120, 200, 280, 360))
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        fake_clock.costs(0.2, 0.0002, 0.015)
        started = fake_clock.now
        with policies(REGEX_ONLY, (ROLE,)):
            result = _handle(search_module, monkeypatch, fake, _event(fake_clock, _browse(10), 0.0), False)

        assert len(fake.queries) > 1
        assert fake_clock.now - started <= search_module.SEARCH_FILL_MAX_SECONDS + 0.015
        assert result["hits"]["total"]["relation"] == "gte"

    @pytest.mark.parametrize("first_answer,api,expected", [
        (0.2, 26.5, (10.0, 10.0)),    # warm: the fill bound for everything
        (6.0, 26.5, (11.0, 10.0)),    # the page gets half the bound after the answer; the rest keep 10 s
        (14.0, 26.5, (19.0, 10.0)),
        (24.0, 26.5, (26.5, 10.0)),   # the page stops at the API deadline
        (0.2, 8.0, (8.0, 8.0)),       # neither passes the API deadline
    ])
    def test_the_page_and_fill_deadlines(self, search_module, first_answer, api, expected):
        started = 1000.0
        page, fill = search_module._check_deadlines(started, started + first_answer, started + api)

        assert (page - started, fill - started) == pytest.approx(expected)


@pytest.mark.unit
class TestAnUncheckedHitIsNeverReturned:
    @pytest.mark.parametrize("simple", [False, True])
    def test_a_page_the_api_deadline_cuts_short_lists_only_checked_readable_hits(self, search_module, monkeypatch,
                                                                                 fake_clock, simple):
        """A role checked after the query; the first fetch answers 25.5 s after the API received the
        request and each check takes 0.25 s, so four hits are checked before the API deadline. The page
        lists the readable ones among those four and nothing past them."""
        decided = []
        decide = search_module.DualIndexResponseProcessor._hit_decision

        def recording(self, hit, claims):
            decided.append(hit["_id"])
            return decide(self, hit, claims)

        monkeypatch.setattr(search_module.DualIndexResponseProcessor, "_hit_decision", recording)
        assets = _sparse_assets(count=60, readable_at=(0, 1, 3, 5, 8, 13))
        result, elapsed = _search(search_module, monkeypatch, fake_clock, assets, 25.5, 0.25, 10, waited=0.0,
                                  constraints=REGEX_ONLY, simple=simple)

        assert decided == ["a0", "a1", "a2", "a3"]
        assert _ids(result) == ["a0", "a1", "a3"]
        assert all(h["_source"]["str_assetname"] == "projXa" for h in result["hits"]["hits"])
        assert result["hits"]["total"]["relation"] == "gte"
        assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]
        assert elapsed <= _cutoff(search_module) + 0.25


@pytest.mark.unit
class TestTheHandlerTakesItsDeadlineFirst:
    @pytest.mark.parametrize("path,handler", [("/search", "handle_post_request"),
                                              ("/search/simple", "handle_simple_post_request")])
    @pytest.mark.parametrize("waited", [2.0, None])
    def test_the_deadline_is_taken_before_the_handler_builds_anything(self, search_module, monkeypatch, fake_clock,
                                                                      path, handler, waited):
        """lambda_handler takes the API deadline before it builds the Tier-1 enforcer (3 s here, cold) and
        the search clients, so the handler's own start-up counts against the API's time, and without a
        receipt time the 20 s run from the handler's start."""
        enforcer = MagicMock()

        def cold_enforce_api(event):
            fake_clock.now += 3.0
            return True

        enforcer.enforceAPI.side_effect = cold_enforce_api
        monkeypatch.setattr(search_module, "CasbinEnforcer", MagicMock(return_value=enforcer))
        for name in ("DualIndexSearchManager", "DatabaseAccessManager", "DualIndexResponseProcessor",
                     "DualIndexQueryBuilder", "SimpleSearchQueryBuilder"):
            monkeypatch.setattr(search_module, name, MagicMock())
        captured = {}

        def handle(event, *args, api_deadline=None):
            captured["api_deadline"] = api_deadline
            return {"statusCode": 200, "body": "{}"}

        monkeypatch.setattr(search_module, handler, handle)
        event = {"requestContext": {"http": {"path": path, "method": "POST"}}, "body": "{}"}
        if waited is not None:
            event["requestContext"]["requestTimeEpoch"] = fake_clock.epoch_ms(seconds_ago=waited)
        entered = fake_clock.now

        search_module.lambda_handler(event, None)

        expected = (entered - waited + _cutoff(search_module) if waited is not None
                    else entered + search_module.SEARCH_REQUEST_MAX_SECONDS)
        assert captured["api_deadline"] == pytest.approx(expected, abs=1e-3)
