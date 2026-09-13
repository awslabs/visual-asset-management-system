# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The OpenSearch constrain/enrich step of POST /search/nlp: imported lazily, applied only when an
OpenSearch mode is on, and degraded to a warning when the file index cannot be reached."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.handlers.vectorsearch import test_vectorSearchService as behaviour
from tests.handlers.vectorsearch.test_vectorSearchService import FakeStore, call, item, rows

svc = behaviour.svc

DOC_A = "db1#a1#a.glb"
DOC_B = "db1#a1#b.glb"


@pytest.fixture
def opensearch(svc, monkeypatch):
    """A stub ``handlers.search.search`` whose manager answers one enriched document (``a.glb``)."""
    svc.opensearch_disabled = False
    stub = types.ModuleType("handlers.search.search")
    client = MagicMock()
    client.search.return_value = {"hits": {"hits": [{"_id": DOC_A, "_source": {"MD_color": "red", "str_previewfilekey": "p.png", "str_key": "stale"}}]}}
    manager = MagicMock()
    manager.client, manager.file_index = client, "vams-files-v3"
    stub.DualIndexSearchManager = MagicMock(return_value=manager)
    builder = MagicMock()
    builder._build_query_clause.return_value = {"bool": {"filter": [{"term": {"str_databaseid": "db1"}}]}}
    stub.DualIndexQueryBuilder = MagicMock(return_value=builder)
    saved = sys.modules.get("handlers.search.search")
    sys.modules["handlers.search.search"] = stub
    documentIds = sys.modules.get("common.indexing.documentIds")
    yield types.SimpleNamespace(stub=stub, client=client, builder=builder, documentIds=documentIds)
    if saved is not None:
        sys.modules["handlers.search.search"] = saved
    else:
        sys.modules.pop("handlers.search.search", None)


def _two_files():
    return FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])


def _search_body(client):
    assert client.search.call_count >= 1
    return client.search.call_args.kwargs["body"]


def test_enrichment_merges_source_and_keeps_unmatched_hits(svc, opensearch):
    rows(svc, ("db1", "a1"))
    status, body = call(svc, _two_files(), {"query": "tractor"})
    assert status == 200
    hits = body["hits"]["hits"]
    assert [h["_source"]["str_key"] for h in hits] == ["a.glb", "b.glb"]
    assert hits[0]["_source"]["MD_color"] == "red" and hits[0]["_source"]["str_previewfilekey"] == "p.png"
    assert "MD_color" not in hits[1]["_source"]
    assert hits[0]["_vector"]["distance"] == 0.1
    assert body["warnings"] == []
    request = _search_body(opensearch.client)
    assert request["size"] == svc.OPENSEARCH_ENRICH_WINDOW
    filters = request["query"]["bool"]["filter"]
    assert {"ids": {"values": [DOC_A, DOC_B]}} in filters
    assert {"term": {"bool_archived": False}} in filters
    assert not opensearch.builder._build_query_clause.called
    assert opensearch.client.search.call_args.kwargs["index"] == "vams-files-v3"


def test_constraints_keep_only_the_documents_the_index_returns(svc, opensearch):
    rows(svc, ("db1", "a1"))
    _, body = call(svc, _two_files(), {"query": "tractor", "tags": ["x"], "includeArchived": True})
    hits = body["hits"]["hits"]
    assert [h["_source"]["str_key"] for h in hits] == ["a.glb"]
    assert body["hits"]["total"]["value"] == 1
    assert not any(w["code"] == "opensearch:fields_ignored" for w in body["warnings"])
    filters = _search_body(opensearch.client)["query"]["bool"]["filter"]
    assert {"term": {"bool_archived": False}} not in filters
    assert any("should" in f.get("bool", {}) for f in filters)
    assert opensearch.builder._build_query_clause.call_count == 1
    keyword_request, databases, index_type = opensearch.builder._build_query_clause.call_args.args
    assert databases == ["db1"] and index_type == "file"
    assert keyword_request.query is None and keyword_request.includeArchived is True


def test_enrichment_failure_degrades_to_a_warning(svc, opensearch):
    rows(svc, ("db1", "a1"))
    opensearch.client.search.side_effect = RuntimeError("connection refused to host-42")
    _, body = call(svc, _two_files(), {"query": "tractor"})
    assert [h["_source"]["str_key"] for h in body["hits"]["hits"]] == ["a.glb", "b.glb"]
    codes = {w["code"]: w["message"] for w in body["warnings"]}
    assert set(codes) == {"opensearch:enrichment_failed"}
    assert "host-42" not in codes["opensearch:enrichment_failed"]


def test_enrich_false_without_constraints_skips_opensearch(svc, opensearch):
    rows(svc, ("db1", "a1"))
    _, body = call(svc, _two_files(), {"query": "tractor", "enrich": False})
    assert len(body["hits"]["hits"]) == 2
    assert not opensearch.stub.DualIndexSearchManager.called


def test_disabled_opensearch_never_imports_the_search_module(svc, opensearch):
    svc.opensearch_disabled = True
    rows(svc, ("db1", "a1"))
    _, body = call(svc, _two_files(), {"query": "tractor", "tags": ["x"]})
    assert len(body["hits"]["hits"]) == 2
    assert not opensearch.stub.DualIndexSearchManager.called
    assert {w["code"] for w in body["warnings"]} == {"opensearch:fields_ignored"}
