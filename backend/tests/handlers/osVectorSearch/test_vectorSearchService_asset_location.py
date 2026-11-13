# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A natural-language asset answer carries what the web's Map view plots (in-dev D83 on #325).

The Map view reads a hit's ``_source.geo_MD_location``, else the ``location`` / ``latitude`` / ``lat`` /
``longitude`` / ``lon`` / ``long`` keys of its ``_source.MD_`` (``extractLocationData`` in
web/src/components/search/utils/locationUtils.ts). An asset hit is built from its best file hit, and a
file document holds its own file's location, never its asset's, so with OpenSearch on the handler reads
the location from the asset's own document in the asset index. The fake index below answers by document
id from in-memory asset and file documents.
"""

import copy
import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, call, item, rows

svc = behaviour.svc

ASSET_INDEX = "vams-assets-v3"
FILE_INDEX = "vams-files-v3"
ASSET_POINT = {"type": "Point", "coordinates": [-122.33, 47.61]}
FILE_POINT = {"type": "Point", "coordinates": [2.35, 48.85]}
ASSET_DOC = {
    "str_assetname": "a1",
    "str_description": "not copied",
    "geo_MD_location": ASSET_POINT,
    "MD_": {"Latitude": "47.61", "LON": "-122.33", "location": '{"lat": 47.61, "lon": -122.33}', "color": "red"},
}


@pytest.fixture
def opensearch(svc):
    """A stub /search module whose manager answers each index by document id from ``assets`` / ``files``."""
    svc.opensearch_disabled = False
    state = types.SimpleNamespace(assets={}, files={}, fail_index=None, calls=[])

    def search(index, body):
        state.calls.append((index, copy.deepcopy(body)))
        if index == state.fail_index:
            raise RuntimeError("connection refused to host-42")
        documents = state.assets if index == ASSET_INDEX else state.files
        ids = body["query"]["bool"]["filter"][0]["ids"]["values"]
        return {"hits": {"hits": [{"_id": doc, "_source": copy.deepcopy(documents[doc])} for doc in ids
                                  if doc in documents]}}

    client = MagicMock()
    client.search.side_effect = search
    manager = MagicMock()
    manager.client, manager.file_index, manager.asset_index = client, FILE_INDEX, ASSET_INDEX
    stub = types.ModuleType("handlers.osSemanticSearch.search")
    stub.DualIndexSearchManager = MagicMock(return_value=manager)
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


def _asset_calls(state):
    return [body for index, body in state.calls if index == ASSET_INDEX]


def _one_asset_two_files():
    return FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])


ASSET_MODE = {"query": "tractor", "entityTypes": ["asset"]}


@pytest.mark.unit
class TestAnAssetHitCarriesItsLocation:
    def test_the_asset_hit_carries_its_assets_location_from_the_asset_index(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.assets["db1#a1"] = ASSET_DOC
        # The best file has a location of its own; the asset hit must carry the asset's
        opensearch.files["db1#a1#a.glb"] = {"geo_MD_location": FILE_POINT, "MD_": {"lat": "48.85", "lon": "2.35"}}
        status, body = call(svc, _one_asset_two_files(), ASSET_MODE)

        assert status == 200
        (hit,) = body["hits"]["hits"]
        assert hit["_index_type"] == "asset"
        assert hit["_source"]["geo_MD_location"] == ASSET_POINT
        assert hit["_source"]["MD_"] == {"Latitude": "47.61", "LON": "-122.33",
                                         "location": '{"lat": 47.61, "lon": -122.33}'}
        assert "str_description" not in hit["_source"]

    def test_the_asset_index_is_asked_for_the_returned_assets_by_id_and_only_their_location(self, svc, opensearch):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        store = FakeStore([item(asset="a1", path="a.glb", distance=0.1), item(asset="a2", path="b.glb", distance=0.2)])
        call(svc, store, ASSET_MODE)

        (request,) = _asset_calls(opensearch)
        assert request["query"] == {"bool": {"filter": [{"ids": {"values": ["db1#a1", "db1#a2"]}}]}}
        assert request["size"] == 2
        assert request["_source"] == ["geo_MD_location", "MD_"]

    def test_an_archived_asset_is_read_from_its_archived_document(self, svc, opensearch):
        svc._load_asset_rows = MagicMock(return_value={("db1", "a1"): {
            "databaseId": "db1#deleted", "assetId": "a1", "assetName": "a1", "assetType": "x", "tags": []}})
        opensearch.assets["db1#deleted#a1"] = {"geo_MD_location": ASSET_POINT}
        opensearch.assets["db1#a1"] = {"geo_MD_location": FILE_POINT}
        store = FakeStore([item(path="a.glb", isArchived="true", distance=0.1)])
        _, body = call(svc, store, {**ASSET_MODE, "includeArchived": True})

        assert body["hits"]["hits"][0]["_source"]["geo_MD_location"] == ASSET_POINT

    def test_only_the_assets_the_caller_may_open_are_looked_up(self, svc, opensearch):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        svc.enforcer.enforce.side_effect = lambda obj, action: obj["assetName"] != "a2"
        opensearch.assets["db1#a2"] = ASSET_DOC
        store = FakeStore([item(asset="a1", path="a.glb", distance=0.1), item(asset="a2", path="b.glb", distance=0.05)])
        _, body = call(svc, store, ASSET_MODE)

        assert [h["_source"]["str_assetid"] for h in body["hits"]["hits"]] == ["a1"]
        assert [r["query"]["bool"]["filter"][0]["ids"]["values"] for r in _asset_calls(opensearch)] == [["db1#a1"]]

    def test_an_asset_without_a_location_gains_no_field(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.assets["db1#a1"] = {"str_assetname": "a1", "MD_": {"color": "red"}}
        _, body = call(svc, _one_asset_two_files(), ASSET_MODE)

        source = body["hits"]["hits"][0]["_source"]
        assert "geo_MD_location" not in source and "MD_" not in source


@pytest.mark.unit
class TestNothingElseChanges:
    def test_the_answer_is_the_same_but_for_the_location_fields(self, svc, opensearch):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        store = FakeStore([item(asset="a1", path="a.glb", distance=0.1), item(asset="a2", path="b.glb", distance=0.2)])
        _, without = call(svc, store, ASSET_MODE)
        opensearch.assets["db1#a1"] = ASSET_DOC
        _, with_location = call(svc, store, ASSET_MODE)

        for hit in with_location["hits"]["hits"]:
            hit["_source"].pop("geo_MD_location", None)
            hit["_source"].pop("MD_", None)
        without.pop("took"), with_location.pop("took")
        assert with_location == without

    def test_a_failed_lookup_keeps_the_hits_and_warns_once(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.fail_index = ASSET_INDEX
        status, body = call(svc, _one_asset_two_files(), ASSET_MODE)

        assert status == 200 and len(body["hits"]["hits"]) == 1
        assert body["warnings"] == [{"code": "opensearch:enrichment_failed", "message": "File details could not be loaded."}]
        assert "host-42" not in json.dumps(body)

    def test_a_failed_lookup_under_a_constraint_answers_503(self, svc, opensearch):
        """An asset answer's constraints are applied by the asset index (D98), so its failure there is a
        constraint that could not be applied: 503, never the unconstrained hits."""
        rows(svc, ("db1", "a1"))
        opensearch.assets["db1#a1"] = ASSET_DOC
        opensearch.fail_index = ASSET_INDEX
        status, body = call(svc, _one_asset_two_files(), {**ASSET_MODE, "tags": ["x"]})

        assert status == 503
        assert "host-42" not in json.dumps(body)

    def test_only_the_assets_of_the_returned_page_are_looked_up(self, svc, opensearch):
        rows(svc, ("db1", "a1"), ("db1", "a2"), ("db1", "a3"))
        store = FakeStore([item(asset=f"a{n}", path=f"f{n}.glb", distance=0.1 * n) for n in (1, 2, 3)])
        _, body = call(svc, store, {**ASSET_MODE, "size": 2})

        assert body["hits"]["total"]["value"] == 3
        assert [r["query"]["bool"]["filter"][0]["ids"]["values"] for r in _asset_calls(opensearch)] == [
            ["db1#a1", "db1#a2"]]

    def test_an_asset_answer_reads_the_asset_index_only(self, svc, opensearch):
        """An asset hit is built from the asset record and its asset's document, so the file index, whose
        documents an asset hit does not show, is not asked: one crossing per window of assets."""
        rows(svc, ("db1", "a1"))
        opensearch.files["db1#a1#a.glb"] = {"geo_MD_location": FILE_POINT}
        opensearch.assets["db1#a1"] = ASSET_DOC
        _, body = call(svc, _one_asset_two_files(), ASSET_MODE)

        assert [index for index, _ in opensearch.calls] == [ASSET_INDEX]
        assert body["hits"]["hits"][0]["_source"]["geo_MD_location"] == ASSET_POINT

    def test_enrich_false_reads_no_location(self, svc, opensearch):
        rows(svc, ("db1", "a1"))
        opensearch.assets["db1#a1"] = ASSET_DOC
        _, body = call(svc, _one_asset_two_files(), {**ASSET_MODE, "enrich": False})

        assert _asset_calls(opensearch) == []
        assert "geo_MD_location" not in body["hits"]["hits"][0]["_source"]

    def test_without_opensearch_the_search_module_is_never_loaded(self, svc, opensearch):
        svc.opensearch_disabled = True
        rows(svc, ("db1", "a1"))
        _, body = call(svc, _one_asset_two_files(), ASSET_MODE)

        assert not opensearch.stub.DualIndexSearchManager.called
        assert "geo_MD_location" not in body["hits"]["hits"][0]["_source"]

    def test_a_file_answer_carries_its_files_location_and_asks_no_asset_index(self, svc, opensearch):
        """File hits already carry the file document's location through the file enrichment."""
        rows(svc, ("db1", "a1"))
        opensearch.files["db1#a1#a.glb"] = {"geo_MD_location": FILE_POINT, "MD_": {"lat": "48.85", "lon": "2.35"}}
        opensearch.assets["db1#a1"] = ASSET_DOC
        _, body = call(svc, _one_asset_two_files(), {"query": "tractor"})

        first = body["hits"]["hits"][0]["_source"]
        assert first["geo_MD_location"] == FILE_POINT and first["MD_"] == {"lat": "48.85", "lon": "2.35"}
        assert _asset_calls(opensearch) == []
