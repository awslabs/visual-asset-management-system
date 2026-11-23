# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp ends its OpenSearch step by keyword search's API deadline.

A cold or slow OpenSearch index can take 10-28 s to answer its first query after idle time. Before the
deadline, the step waited for it (and retried a timeout), so the request ran past API Gateway's 29-second
limit and the caller got a 504 instead of the vector hits. Now each OpenSearch query waits at most the time
the deadline leaves, on a client that does not retry, and the step degrades as any OpenSearch failure does:
the hits with the ``opensearch:enrichment_failed`` warning, or 503 under an OpenSearch-only constraint.

The handler runs on a fake clock. The stub /search module's ``_api_check_deadline`` answers the deadline
of a request API Gateway received ``WAITED`` seconds before the handler started, and the fake index takes
``answer_seconds`` to answer a query: it answers within a ``request_timeout`` that is long enough, raises
the client's ``ConnectionTimeout`` once a shorter one runs out, and answers late when no timeout is given.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest
from opensearchpy.exceptions import ConnectionTimeout

from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, call, item, rows

svc = behaviour.svc

# API Gateway's 29-second floor less keyword search's 2.5-second reserve (search.py _api_check_deadline)
API_CUTOFF = 29 - 2.5
# The request reached the handler this long after API Gateway received it (cold start, authorizer)
WAITED = 3.0
# A cold index's first answer, as seen live (#1590, #1763)
COLD_ANSWER_SECONDS = 25.0


class Clock:
    """``time`` for the handler: ``monotonic`` reads ``now``, which the fake index moves."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1_795_000_000.0 + self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(svc, monkeypatch):
    fake = Clock()
    monkeypatch.setattr(svc, "time", fake)
    return fake


@pytest.fixture
def slow_index(svc, clock):
    """A stub /search module whose file and asset indexes answer every id they are asked about, each query
    taking ``state.answer_seconds`` (a list is used one entry per query, in order; an entry may be a
    function of the clock, called when its query is made)."""
    svc.opensearch_disabled = False
    state = types.SimpleNamespace(answer_seconds=COLD_ANSWER_SECONDS, calls=[], started=None)

    def search(index, body, request_timeout=None, **_):
        seconds = state.answer_seconds.pop(0) if isinstance(state.answer_seconds, list) else state.answer_seconds
        seconds = seconds() if callable(seconds) else seconds
        state.calls.append({"index": index, "request_timeout": request_timeout, "at": clock.now})
        if request_timeout is not None and request_timeout < seconds:
            clock.now += request_timeout
            raise ConnectionTimeout("TIMEOUT", "Read timed out", Exception("read timeout"))
        clock.now += seconds
        ids = body["query"]["bool"]["filter"][0]["ids"]["values"]
        return {"hits": {"hits": [{"_id": doc, "_source": {"MD_color": "red"}} for doc in ids]}}

    client = MagicMock()
    client.search.side_effect = search
    manager = MagicMock()
    manager.client, manager.file_index, manager.asset_index = client, "vams-files-v3", "vams-assets-v3"
    stub = types.ModuleType("handlers.osSemanticSearch.search")
    stub.DualIndexSearchManager = MagicMock(return_value=manager)

    def api_check_deadline(event):
        state.started = clock.now
        return clock.now - WAITED + API_CUTOFF

    stub._api_check_deadline = MagicMock(side_effect=api_check_deadline)
    builder = MagicMock()
    builder._build_query_clause.return_value = {"bool": {"filter": [{"term": {"str_databaseid": "db1"}}]}}
    stub.DualIndexQueryBuilder = MagicMock(return_value=builder)
    saved = sys.modules.get("handlers.osSemanticSearch.search")
    sys.modules["handlers.osSemanticSearch.search"] = stub
    state.stub = stub
    yield state
    if saved is not None:
        sys.modules["handlers.osSemanticSearch.search"] = saved
    else:
        sys.modules.pop("handlers.osSemanticSearch.search", None)


def _deadline(state):
    return state.started - WAITED + API_CUTOFF


def _two_files(svc):
    rows(svc, ("db1", "a1"))
    return FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])


def _codes(body):
    return [warning["code"] for warning in body["warnings"]]


@pytest.mark.unit
class TestASlowIndexCostsTheFileDetailsNotTheRequest:
    def test_the_hits_are_answered_with_the_warning_by_the_api_deadline(self, svc, clock, slow_index):
        status, body = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        assert status == 200
        assert [hit["_source"]["str_key"] for hit in body["hits"]["hits"]] == ["a.glb", "b.glb"]
        assert "MD_color" not in body["hits"]["hits"][0]["_source"]
        assert _codes(body) == ["opensearch:enrichment_failed"]
        assert clock.now <= _deadline(slow_index), "the step ran past the API deadline"
        assert slow_index.calls[0]["request_timeout"] == pytest.approx(_deadline(slow_index) - slow_index.calls[0]["at"])

    def test_an_asset_answer_reads_the_asset_index_by_the_api_deadline(self, svc, clock, slow_index):
        status, body = call(svc, _two_files(svc), {"query": "tractor", "entityTypes": ["asset"], "includeSegments": False})
        assert status == 200 and len(body["hits"]["hits"]) == 1
        assert _codes(body) == ["opensearch:enrichment_failed"]
        assert [c["index"] for c in slow_index.calls] == ["vams-assets-v3"]
        assert clock.now <= _deadline(slow_index)

    def test_under_a_constraint_the_answer_is_503_by_the_api_deadline(self, svc, clock, slow_index):
        status, body = call(svc, _two_files(svc), {"query": "tractor", "tags": ["x"], "includeSegments": False})
        assert status == 503
        assert body["message"] == svc.CONSTRAINTS_FAILED_MESSAGE
        assert clock.now <= _deadline(slow_index)

    def test_a_warm_index_still_enriches(self, svc, clock, slow_index):
        slow_index.answer_seconds = 0.2
        status, body = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        assert status == 200 and _codes(body) == []
        assert body["hits"]["hits"][0]["_source"]["MD_color"] == "red"


@pytest.mark.unit
class TestEveryQueryWaitsOnlyTheTimeLeft:
    def _many_files(self, svc):
        """120 file hits: two enrichment windows of OPENSEARCH_ENRICH_WINDOW and 20 ids."""
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 3)
        rows(svc, ("db1", "a1"), ("db2", "a1"))
        return FakeStore([item(db=db, path=f"f{i:03d}.glb", distance=0.1 + i / 1000)
                          for db in ("db1", "db2") for i in range(60)])

    def test_the_second_window_gets_only_what_the_first_left(self, svc, clock, slow_index):
        slow_index.answer_seconds = [15.0, 15.0]
        status, body = call(svc, self._many_files(svc), {"query": "tractor", "size": 100, "includeSegments": False})
        assert status == 200 and _codes(body) == ["opensearch:enrichment_failed"]
        first, second = slow_index.calls
        assert second["request_timeout"] == pytest.approx(_deadline(slow_index) - second["at"])
        assert second["request_timeout"] < first["request_timeout"]
        assert clock.now <= _deadline(slow_index)

    def test_no_query_starts_with_too_little_time_left(self, svc, clock, slow_index):
        # The first window answers just inside the deadline, leaving less than the step's minimum.
        slow_index.answer_seconds = [
            lambda: _deadline(slow_index) - clock.now - svc.OPENSEARCH_STEP_MIN_SECONDS / 2,
            1.0,
        ]
        status, body = call(svc, self._many_files(svc), {"query": "tractor", "size": 100, "includeSegments": False})
        assert status == 200 and _codes(body) == ["opensearch:enrichment_failed"]
        assert len(slow_index.calls) == 1, "a query was started with less than OPENSEARCH_STEP_MIN_SECONDS left"
        assert clock.now <= _deadline(slow_index)


@pytest.mark.unit
class TestTheDeadlineAndTheClient:
    def test_the_deadline_is_keyword_searchs_taken_from_the_event_when_the_handler_starts(self, svc, clock, slow_index):
        status, _ = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        assert status == 200
        assert slow_index.stub._api_check_deadline.call_count == 1
        (event,), _ = slow_index.stub._api_check_deadline.call_args
        assert event["requestContext"]["http"]["path"] == "/search/nlp"
        assert slow_index.started == 1000.0, "taken before the scan, the embedding and the vector searches"

    def test_the_step_uses_one_manager_per_container_whose_client_does_not_retry(self, svc, clock, slow_index):
        slow_index.answer_seconds = 0.2
        for _ in range(2):
            status, _ = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
            assert status == 200
        assert slow_index.stub.DualIndexSearchManager.call_count == 1
        assert slow_index.stub.DualIndexSearchManager.call_args.kwargs == {"max_retries": 0, "retry_on_timeout": False}

    def test_a_manager_without_a_client_is_not_kept(self, svc, clock, slow_index):
        down = MagicMock()
        down.is_available.return_value = False
        down.client = None
        up = slow_index.stub.DualIndexSearchManager.return_value
        slow_index.stub.DualIndexSearchManager.side_effect = [down, up]
        slow_index.answer_seconds = 0.2
        _, first = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        _, second = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        assert _codes(first) == ["opensearch:enrichment_failed"] and _codes(second) == []

    def test_without_opensearch_no_deadline_is_taken(self, svc, clock, slow_index):
        svc.opensearch_disabled = True
        status, body = call(svc, _two_files(svc), {"query": "tractor", "includeSegments": False})
        assert status == 200 and _codes(body) == []
        assert slow_index.stub._api_check_deadline.call_count == 0 and slow_index.calls == []
