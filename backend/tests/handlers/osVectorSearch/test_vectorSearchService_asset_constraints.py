# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A natural-language asset answer applies its OpenSearch-only constraints to the asset index (D98, #325).

Keyword search applies an asset search's ``filters``, ``metadataQuery``, ``tags`` and ``geoSearch`` to
the asset index, which holds the asset metadata; a file document holds its own file's metadata only. A
natural-language asset answer does the same: the constraint clauses are built for the asset index by the
/search query builder and sent with the ids of the assets of the authorized hits; a file answer is
constrained by the file index as before. The fake client answers each index from in-memory documents
and evaluates the builder's clauses with the model of how OpenSearch answers them
(``query_string_oracle``), on both engines.
"""

import copy
import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.handlers.authz import query_string_oracle as oracle
from tests.handlers.authz.search_authz_harness import load_search_module
from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, call, item, rows

svc = behaviour.svc

ASSET_INDEX = "vams-assets-v3"
FILE_INDEX = "vams-files-v3"
# a1 and a3 carry ProjectCode PRJ-325 on the asset; their files carry no asset metadata.
ASSETS = {
    "db1#a1": oracle.PROBE_ASSETS["fz-a/cad-part"],
    "db1#a2": oracle.PROBE_ASSETS["fz-b/checklist"],
    "db1#a3": oracle.PROBE_ASSETS["fz-a/operator-notes"],
}
FILES = {
    "db1#a1#f1.glb": {"str_key": "/f1.glb", "MD_": {}},
    "db1#a2#f2.glb": {"str_key": "/f2.glb", "MD_": {}},
    "db1#a3#f3.glb": {"str_key": "/f3.glb", "MD_": {"category": "maintenance log"}},
}
PROJECT = {"query": "maintenance", "metadataQuery": "ProjectCode:PRJ-325"}


@pytest.fixture
def search_module(svc):
    """search.py with the real builder; loaded after the service, whose load binds the backend modules
    search.py imports, so the file also runs on its own."""
    return load_search_module("search_under_test_nlp_asset_constraints")


@pytest.fixture(params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


@pytest.fixture
def opensearch(svc, search_module, engine):
    """The real query builder behind a stub /search module whose client evaluates every clause after the
    ``ids`` filter against the index's in-memory documents."""
    svc.opensearch_disabled = False
    state = types.SimpleNamespace(calls=[], fail_index=None)

    def search(index, body):
        state.calls.append((index, copy.deepcopy(body)))
        if index == state.fail_index:
            raise RuntimeError("connection refused")
        documents = ASSETS if index == ASSET_INDEX else FILES
        ids_clause, *clauses = body["query"]["bool"]["filter"]
        found = [doc for doc in ids_clause["ids"]["values"] if doc in documents
                 and all(oracle.clause_matches(clause, documents[doc], engine) for clause in clauses
                         if "term" not in clause)]
        return {"hits": {"hits": [{"_id": doc, "_source": copy.deepcopy(documents[doc])} for doc in found]}}

    client = MagicMock()
    client.search.side_effect = search
    manager = MagicMock()
    manager.client, manager.file_index, manager.asset_index = client, FILE_INDEX, ASSET_INDEX
    stub = types.ModuleType("handlers.osSemanticSearch.search")
    stub.DualIndexSearchManager = MagicMock(return_value=manager)
    stub.DualIndexQueryBuilder = search_module.DualIndexQueryBuilder
    saved = sys.modules.get("handlers.osSemanticSearch.search")
    sys.modules["handlers.osSemanticSearch.search"] = stub
    yield state
    if saved is not None:
        sys.modules["handlers.osSemanticSearch.search"] = saved
    else:
        sys.modules.pop("handlers.osSemanticSearch.search", None)


def _three_assets(svc):
    rows(svc, ("db1", "a1"), ("db1", "a2"), ("db1", "a3"))
    return FakeStore([item(asset=f"a{n}", path=f"f{n}.glb", distance=0.1 * n) for n in (1, 2, 3)])


def _indexes(state):
    return [index for index, _ in state.calls]


@pytest.mark.unit
class TestAnAssetAnswerIsConstrainedByTheAssetIndex:
    def test_an_asset_metadata_filter_finds_the_assets_carrying_it(self, svc, opensearch):
        status, body = call(svc, _three_assets(svc), {**PROJECT, "entityTypes": ["asset"]})

        assert status == 200
        assert [hit["_source"]["str_assetid"] for hit in body["hits"]["hits"]] == ["a1", "a3"]
        assert body["hits"]["total"]["value"] == 2
        assert _indexes(opensearch) == [ASSET_INDEX]

    def test_the_asset_index_receives_the_clauses_keyword_search_builds_for_it(
            self, svc, search_module, opensearch):
        call(svc, _three_assets(svc), {**PROJECT, "entityTypes": ["asset"]})
        from models.vectorsearch import NlpSearchRequestModel

        expected = svc._opensearch_constraints(
            NlpSearchRequestModel(**PROJECT, entityTypes=["asset"]), search_module, ["db1"], "asset")
        ((_, body),) = opensearch.calls
        assert body["query"]["bool"]["filter"] == [{"ids": {"values": ["db1#a1", "db1#a2", "db1#a3"]}}] + expected
        assert body["size"] == 3

    def test_only_the_assets_the_caller_may_open_are_asked(self, svc, opensearch):
        svc.enforcer.enforce.side_effect = lambda obj, action: obj["assetName"] != "a3"
        _, body = call(svc, _three_assets(svc), {**PROJECT, "entityTypes": ["asset"]})

        assert [hit["_source"]["str_assetid"] for hit in body["hits"]["hits"]] == ["a1"]
        ((_, request),) = opensearch.calls
        assert request["query"]["bool"]["filter"][0] == {"ids": {"values": ["db1#a1", "db1#a2"]}}

    def test_a_failure_under_the_constraint_answers_503(self, svc, opensearch):
        opensearch.fail_index = ASSET_INDEX
        status, _ = call(svc, _three_assets(svc), {**PROJECT, "entityTypes": ["asset"]})
        assert status == 503


@pytest.mark.unit
class TestAFileAnswerIsConstrainedByTheFileIndex:
    def test_an_asset_metadata_filter_matches_no_file_document(self, svc, opensearch):
        _, body = call(svc, _three_assets(svc), PROJECT)

        assert body["hits"]["hits"] == []
        assert _indexes(opensearch) == [FILE_INDEX]

    def test_a_file_metadata_filter_finds_its_file(self, svc, opensearch):
        _, body = call(svc, _three_assets(svc), {"query": "maintenance", "metadataQuery": "category:maintenance log"})

        assert [hit["_source"]["str_assetid"] for hit in body["hits"]["hits"]] == ["a3"]
        assert _indexes(opensearch) == [FILE_INDEX]


@pytest.mark.unit
def test_the_asset_index_is_asked_in_windows_of_100_each_sized_to_its_own_ids(svc, search_module, opensearch):
    """The step itself, over 150 assets: two reads, of 100 and 50 ids, every asset answered."""
    from models.vectorsearch import NlpSearchRequestModel

    request = NlpSearchRequestModel(**PROJECT, entityTypes=["asset"], size=10)
    hits = [{"_source": {"str_databaseid": "db1", "str_assetid": f"b{n}"}, "asset": {"databaseId": "db1"}}
            for n in range(150)]
    manager = sys.modules["handlers.osSemanticSearch.search"].DualIndexSearchManager()
    kept = svc._asset_index_step(request, search_module, manager, hits)

    sizes = [(len(body["query"]["bool"]["filter"][0]["ids"]["values"]), body["size"]) for _, body in opensearch.calls]
    assert sizes == [(100, 100), (50, 50)]
    assert kept == [], "no b* asset carries the value"
