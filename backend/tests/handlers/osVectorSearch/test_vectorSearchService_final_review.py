# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp: the diagnostic counts, the denial audit, the OpenSearch constraint failure, deleted
databases on the unpartitioned call, throttling and timeouts, the preview key and refusal logging.

Uses the behaviour suite's fixture and fakes, so the handler under test is the real module loaded by path.
"""

import contextlib
import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, call, chunk, item, rows

svc = behaviour.svc


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "slow down"}}, "Operation")


class TestDiagnosticCountsOnlyCountOpenableFiles:
    """nlp.candidatesEvaluated and nlp.itemsCollapsed count the vector items of the files the caller may
    open: an asset-constrained role sees the same numbers as a caller whose index holds only those files."""

    def _two_assets(self):
        # a1: a whole-file item and two chunks; a2 (refused by the role): a whole-file item and three chunks.
        return FakeStore([
            item(asset="a1", distance=0.10), chunk("c000001", 0.11, asset="a1"), chunk("c000002", 0.12, asset="a1"),
            item(asset="a2", distance=0.05), chunk("c000001", 0.06, asset="a2"), chunk("c000002", 0.07, asset="a2"),
            chunk("c000003", 0.08, asset="a2"),
        ])

    def test_the_counts_equal_those_of_the_authorized_set(self, svc):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        svc.enforcer.enforce.side_effect = lambda obj, action: obj["assetName"] != "a2"
        status, body = call(svc, self._two_assets(), {"query": "tractor"})
        assert status == 200
        assert [h["_source"]["str_assetid"] for h in body["hits"]["hits"]] == ["a1"]
        assert body["nlp"]["candidatesEvaluated"] == 3
        assert body["nlp"]["itemsCollapsed"] == 2

    def test_a_role_that_opens_nothing_reports_zero(self, svc):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        svc.enforcer.enforce.side_effect = lambda obj, action: False
        status, body = call(svc, self._two_assets(), {"query": "tractor"})
        assert status == 200 and body["hits"]["hits"] == []
        assert body["nlp"]["candidatesEvaluated"] == 0 and body["nlp"]["itemsCollapsed"] == 0

    def test_an_unconstrained_role_counts_every_item(self, svc):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        _, body = call(svc, self._two_assets(), {"query": "tractor"})
        assert body["nlp"]["candidatesEvaluated"] == 7 and body["nlp"]["itemsCollapsed"] == 5


class TestDenialAuditIsBatched:
    """The database scan and the per-hit checks run inside deferred_authorization_audit(), so N refused
    databases and M refused hits are written when the search ends, in one flush, not N+M calls."""

    def test_refusals_are_recorded_inside_one_deferred_block(self, svc, monkeypatch):
        state = {"deferring": False, "deferred": 0, "inline": 0, "flushes": 0}

        @contextlib.contextmanager
        def deferring():
            state["deferring"] = True
            try:
                yield
            finally:
                state["deferring"] = False
                state["flushes"] += 1

        def record():
            state["deferred" if state["deferring"] else "inline"] += 1

        refused_databases = 40

        def scan(claims_and_roles, show_deleted=False):
            for _ in range(refused_databases):
                record()  # each refused database row, as enforce() audits it
            return ["db1"], refused_databases + 1

        def enforce(obj, action):
            allowed = obj["assetName"] == "a0"
            if not allowed:
                record()
            return allowed

        monkeypatch.setattr(svc, "deferred_authorization_audit", deferring)
        svc.access.get_accessible_databases_with_count.side_effect = scan
        svc.enforcer.enforce.side_effect = enforce
        refused_hits = 12
        rows(svc, *[("db1", f"a{n}") for n in range(refused_hits + 1)])
        store = FakeStore([item(asset=f"a{n}", path=f"f{n}.glb", distance=0.1 + n / 100) for n in range(refused_hits + 1)])
        status, body = call(svc, store, {"query": "tractor", "includeSegments": False})
        assert status == 200 and len(body["hits"]["hits"]) == 1
        assert state == {"deferring": False, "deferred": refused_databases + refused_hits, "inline": 0, "flushes": 1}


@pytest.fixture
def opensearch(svc):
    """A stub /search module whose file index answers one document for every id it is asked about."""
    svc.opensearch_disabled = False
    stub = types.ModuleType("handlers.osSemanticSearch.search")
    client = MagicMock()
    client.search.side_effect = lambda index, body: {"hits": {"hits": [
        {"_id": doc, "_source": {"MD_color": "red"}} for doc in body["query"]["bool"]["filter"][0]["ids"]["values"]]}}
    manager = MagicMock()
    manager.client, manager.file_index = client, "vams-files-v3"
    stub.DualIndexSearchManager = MagicMock(return_value=manager)
    builder = MagicMock()
    builder._build_query_clause.return_value = {"bool": {"filter": [{"term": {"str_databaseid": "db1"}}]}}
    stub.DualIndexQueryBuilder = MagicMock(return_value=builder)
    saved = sys.modules.get("handlers.osSemanticSearch.search")
    sys.modules["handlers.osSemanticSearch.search"] = stub
    yield types.SimpleNamespace(stub=stub, client=client)
    if saved is not None:
        sys.modules["handlers.osSemanticSearch.search"] = saved
    else:
        sys.modules.pop("handlers.osSemanticSearch.search", None)


def _two_files():
    return FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])


class TestConstraintsFailClosed:
    """A request with an OpenSearch-only constraint is answered only with the constraint applied: when the
    OpenSearch step fails it answers 503 with a generic message, never the unconstrained hits."""

    @pytest.mark.parametrize("constraint", [
        {"tags": ["x"]},
        {"filters": [{"query_string": {"query": "str_assettype:x"}}]},
        {"metadataQuery": "MD_color:red"},
        {"geoSearch": {"point": {"lat": 1.0, "lon": 2.0, "radiusMeters": 10}}},
    ])
    def test_a_constraint_plus_an_opensearch_failure_is_an_error(self, svc, opensearch, constraint):
        rows(svc, ("db1", "a1"))
        opensearch.client.search.side_effect = RuntimeError("connection refused to host-42")
        status, body = call(svc, _two_files(), {"query": "tractor", **constraint})
        assert status == 503
        assert body == {"message": svc.CONSTRAINTS_FAILED_MESSAGE}
        assert "host-42" not in json.dumps(body)

    def test_a_failure_to_build_the_constraint_is_an_error(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.stub.DualIndexQueryBuilder.side_effect = RuntimeError("builder broke")
        status, body = call(svc, _two_files(), {"query": "tractor", "tags": ["x"]})
        assert status == 503 and body == {"message": svc.CONSTRAINTS_FAILED_MESSAGE}

    def test_no_constraint_plus_a_failure_keeps_the_hits_and_warns(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.client.search.side_effect = RuntimeError("connection refused to host-42")
        status, body = call(svc, _two_files(), {"query": "tractor"})
        assert status == 200
        assert [h["_source"]["str_key"] for h in body["hits"]["hits"]] == ["a.glb", "b.glb"]
        assert body["warnings"] == [{"code": "opensearch:enrichment_failed", "message": "File details could not be loaded."}]

    def test_a_bad_constraint_is_a_400_before_any_search(self, svc, opensearch):
        store = _two_files()
        status, body = call(svc, store, {"query": "tractor", "filters": [{"bogus": 1}]})
        assert status == 400
        assert store.calls == [] and not svc.embed_text.called
        assert not opensearch.stub.DualIndexSearchManager.called

    def test_an_unknown_file_class_is_a_400_before_any_search(self, svc):
        store = _two_files()
        status, body = call(svc, store, {"query": "tractor", "fileClasses": ["3d-model"]})
        assert status == 400 and "3d-model" not in body["message"]
        assert store.calls == [] and not svc.embed_text.called


class TestPreviewKey:
    def test_the_file_index_preview_key_wins_over_the_one_captured_at_embedding(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.client.search.side_effect = lambda index, body: {"hits": {"hits": [
            {"_id": doc, "_source": {"str_previewfilekey": "/f.glb.previewFile.png"}}
            for doc in body["query"]["bool"]["filter"][0]["ids"]["values"]]}}
        store = FakeStore([item(path="f.glb", previewFileKey="/f.glb.previewFile.gif")])
        _, body = call(svc, store, {"query": "tractor", "includeSegments": False})
        assert body["hits"]["hits"][0]["_source"]["str_previewfilekey"] == "/f.glb.previewFile.png"

    def test_the_vector_preview_key_stays_when_the_index_has_none(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(path="f.glb", previewFileKey="/f.glb.previewFile.gif")])
        _, body = call(svc, store, {"query": "tractor", "includeSegments": False})
        assert body["hits"]["hits"][0]["_source"]["str_previewfilekey"] == "/f.glb.previewFile.gif"


class TestDeletedDatabasesOnTheUnpartitionedCall:
    """A caller who reads every live database gets one call with no databaseId filter; an archived file of
    a database deleted since is not answered, as it is not for a caller whose calls filter by database."""

    def _store(self):
        return FakeStore([
            item(db="db1", asset="a1", isArchived="true", distance=0.1),
            item(db="gone", asset="g1", isArchived="true", distance=0.05),
        ])

    def _archived_rows(self, svc):
        def batch_get_item(RequestItems):
            keys = RequestItems[svc.asset_storage_table_name]["Keys"]
            found = [{"databaseId": k["databaseId"], "assetId": k["assetId"], "assetName": k["assetId"],
                      "assetType": "x", "tags": []} for k in keys if k["databaseId"].endswith("#deleted")]
            return {"Responses": {svc.asset_storage_table_name: found}}

        svc.dynamodb.batch_get_item = MagicMock(side_effect=batch_get_item)

    def test_the_unpartitioned_call_answers_live_databases_only(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = (["db1"], 1)
        self._archived_rows(svc)
        store = self._store()
        status, body = call(svc, store, {"query": "tractor", "includeArchived": True, "includeSegments": False})
        assert status == 200
        assert all("databaseId" not in c for c in store.calls)  # the one unpartitioned call
        assert [h["_source"]["str_databaseid"] for h in body["hits"]["hits"]] == ["db1"]
        keys = [k for c in svc.dynamodb.batch_get_item.call_args_list
                for k in c.kwargs["RequestItems"][svc.asset_storage_table_name]["Keys"]]
        assert all(not k["databaseId"].startswith("gone") for k in keys)

    def test_a_partial_reach_caller_answers_the_same(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = (["db1"], 2)
        self._archived_rows(svc)
        _, body = call(svc, self._store(), {"query": "tractor", "includeArchived": True, "includeSegments": False})
        assert [h["_source"]["str_databaseid"] for h in body["hits"]["hits"]] == ["db1"]


class TestThrottlingAndTimeouts:
    @pytest.mark.parametrize("code", ["ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"])
    def test_a_throttled_embedding_is_a_503_that_says_try_again(self, svc, code):
        svc.embed_text.side_effect = _client_error(code)
        status, body = call(svc, _two_files(), {"query": "tractor"})
        assert status == 503 and body == {"message": svc.SEARCH_BUSY_MESSAGE}

    def test_a_throttled_search_vectors_call_is_a_503(self, svc):
        store = MagicMock()
        store.search.side_effect = _client_error("ProvisionedThroughputExceededException")
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 503 and body == {"message": svc.SEARCH_BUSY_MESSAGE}

    def test_a_timed_out_embedding_is_a_503(self, svc):
        svc.embed_text.side_effect = ReadTimeoutError(endpoint_url="https://bedrock-runtime")
        status, body = call(svc, _two_files(), {"query": "tractor"})
        assert status == 503 and body == {"message": svc.SEARCH_BUSY_MESSAGE}

    def test_any_other_client_error_stays_an_internal_error(self, svc):
        store = MagicMock()
        store.search.side_effect = _client_error("InternalServerError")
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 500 and "slow down" not in json.dumps(body)

    def test_the_query_embedding_uses_the_bounded_client(self, svc):
        call(svc, _two_files(), {"query": "tractor"})
        assert svc.embed_text.call_args.kwargs["client"] is svc.bedrock_runtime_client
        config = svc.BEDROCK_QUERY_CONFIG
        assert (config.connect_timeout + config.read_timeout) * config.retries["max_attempts"] < 29

    def test_unprocessed_asset_keys_are_retried_with_back_off(self, svc, monkeypatch):
        sleeps = []
        monkeypatch.setattr(svc.time, "sleep", sleeps.append)
        table = svc.asset_storage_table_name
        answers = iter([
            {"Responses": {table: []}, "UnprocessedKeys": {table: {"Keys": [{"databaseId": "db1", "assetId": "a1"}]}}},
            {"Responses": {table: []}, "UnprocessedKeys": {table: {"Keys": [{"databaseId": "db1", "assetId": "a1"}]}}},
            {"Responses": {table: [{"databaseId": "db1", "assetId": "a1", "assetName": "a1", "tags": []}]}},
        ])
        svc.dynamodb.batch_get_item = MagicMock(side_effect=lambda RequestItems: next(answers))
        assert len(svc._batch_get([{"databaseId": "db1", "assetId": "a1"}])) == 1
        assert sleeps == [svc.BATCH_GET_BACKOFF_BASE_SECONDS, svc.BATCH_GET_BACKOFF_BASE_SECONDS * 2]

    def test_keys_left_unprocessed_past_the_attempts_answer_503(self, svc, monkeypatch):
        monkeypatch.setattr(svc.time, "sleep", lambda seconds: None)
        table = svc.asset_storage_table_name
        stuck = {"Responses": {table: []}, "UnprocessedKeys": {table: {"Keys": [{"databaseId": "db1", "assetId": "a1"}]}}}
        calls = []

        def batch_get_item(RequestItems):
            calls.append(RequestItems)
            if len(calls) > 50:
                raise AssertionError("BatchGetItem re-sent unprocessed keys without a bound")
            return stuck

        svc.dynamodb.batch_get_item = MagicMock(side_effect=batch_get_item)
        status, body = call(svc, _two_files(), {"query": "tractor"})
        assert status == 503 and body == {"message": svc.SEARCH_BUSY_MESSAGE}
        assert svc.dynamodb.batch_get_item.call_count == svc.BATCH_GET_MAX_ATTEMPTS


class TestRefusalLogging:
    def test_a_validation_refusal_is_not_logged_as_an_error(self, svc):
        svc.logger = MagicMock()
        status, _ = call(svc, _two_files(), {"query": "tractor", "size": 0})
        assert status == 400
        assert not svc.logger.exception.called and not svc.logger.error.called
        assert svc.logger.info.called or svc.logger.warning.called


class TestIndexBuildingAnswer:
    def test_the_503_goes_through_the_shared_response_function(self, svc, monkeypatch):
        seen = []
        real = svc.service_unavailable
        monkeypatch.setattr(svc, "service_unavailable", lambda **kw: seen.append(kw["body"]) or real(**kw))
        store = MagicMock()
        store.search.side_effect = svc.VectorIndexNotReady("being created")
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 503 and body == {"message": svc.INDEX_BUILDING_MESSAGE}
        assert seen == [{"message": svc.INDEX_BUILDING_MESSAGE}]
