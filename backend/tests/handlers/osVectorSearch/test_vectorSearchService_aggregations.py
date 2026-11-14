# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp aggregations: OpenSearch-style terms buckets over the authorized answer.

The web filter panels read ``aggregations.<field>.buckets`` ({key, doc_count}) the way they read a keyword
search response: ``str_fileext`` in Files mode, ``str_assettype`` in Assets mode, ``str_databaseid`` and
``list_tags`` in both. The buckets count every authorized hit the answer holds, before the ``size`` cut.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService_opensearch import opensearch  # noqa: F401 (fixture)

svc = behaviour.svc  # the behaviour module's fixture: auth surface, embedding and database access replaced
item, call, FakeStore = behaviour.item, behaviour.call, behaviour.FakeStore


def _asset_rows(svc, assets):
    """Asset rows by (databaseId, assetId), each with the given assetType and tags."""
    svc._load_asset_rows = MagicMock(return_value={
        (db, asset): {"databaseId": db, "assetId": asset, "assetName": asset, "assetType": asset_type, "tags": tags}
        for db, asset, asset_type, tags in assets
    })


def _two_databases(svc):
    svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 2)
    _asset_rows(svc, [
        ("db1", "a1", ".txt", ["survey", "pump"]),
        ("db2", "a2", ".glb", ["pump"]),
        ("db1", "a3", ".txt", ["secret"]),
    ])
    return FakeStore([
        item(db="db1", asset="a1", path="/notes.txt", cls="text", fileExt="txt", distance=0.1),
        item(db="db1", asset="a1", path="/LICENSE", cls="other", fileExt="none", distance=0.2),
        item(db="db1", asset="a1", path="/more.txt", cls="text", fileExt="txt", distance=0.3),
        item(db="db2", asset="a2", path="/pump.glb", cls="mesh", fileExt="glb", distance=0.4),
        item(db="db1", asset="a3", path="/hidden.txt", cls="text", fileExt="txt", distance=0.5),
    ])


def _buckets(body, field):
    return body["aggregations"][field]["buckets"]


class TestFilesMode:
    def test_buckets_count_every_authorized_hit_before_the_size_cut(self, svc):
        store = _two_databases(svc)
        svc.enforcer.enforce.side_effect = lambda obj, action: obj["assetName"] != "a3"
        status, body = call(svc, store, {"query": "pump notes", "size": 3, "includeSegments": False})
        assert status == 200
        assert len(body["hits"]["hits"]) == 3 and body["hits"]["total"]["value"] == 4
        assert set(body["aggregations"]) == {"str_fileext", "str_databaseid", "list_tags"}
        # The extension-less file is counted in no extension bucket, as the keyword index stores no
        # extension for it; the denied asset's file is counted nowhere.
        assert _buckets(body, "str_fileext") == [{"key": "txt", "doc_count": 2}, {"key": "glb", "doc_count": 1}]
        assert _buckets(body, "str_databaseid") == [{"key": "db1", "doc_count": 3}, {"key": "db2", "doc_count": 1}]
        assert _buckets(body, "list_tags") == [{"key": "pump", "doc_count": 4}, {"key": "survey", "doc_count": 3}]
        assert body["aggregationTotal"] == sum(b["doc_count"] for b in _buckets(body, "str_databaseid"))

    def test_extension_keys_are_lower_case_without_the_dot(self, svc):
        _asset_rows(svc, [("db1", "a1", ".pdf", [])])
        store = FakeStore([item(path="/Pump_Manual.PDF", cls="document", fileExt="pdf")])
        _, body = call(svc, store, {"query": "pump manual", "includeSegments": False})
        assert _buckets(body, "str_fileext") == [{"key": "pdf", "doc_count": 1}]
        assert _buckets(body, "list_tags") == []

    def test_equal_counts_are_ordered_by_key(self, svc):
        _asset_rows(svc, [("db1", "a1", ".x", [])])
        store = FakeStore([
            item(path="/b.png", cls="image", fileExt="png", distance=0.1),
            item(path="/a.mp4", cls="video", fileExt="mp4", distance=0.2),
            item(path="/c.png", cls="image", fileExt="png", distance=0.3),
            item(path="/d.glb", cls="mesh", fileExt="glb", distance=0.4),
        ])
        _, body = call(svc, store, {"query": "site", "includeSegments": False})
        assert [b["key"] for b in _buckets(body, "str_fileext")] == ["png", "glb", "mp4"]

    def test_the_aggregation_has_the_terms_shape(self, svc):
        _asset_rows(svc, [("db1", "a1", ".glb", ["t"])])
        _, body = call(svc, FakeStore([item()]), {"query": "tractor"})
        assert body["aggregations"]["str_databaseid"] == {
            "doc_count_error_upper_bound": 0,
            "sum_other_doc_count": 0,
            "buckets": [{"key": "db1", "doc_count": 1}],
        }

    def test_buckets_beyond_the_limit_are_summed_as_other(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "AGGREGATION_BUCKET_LIMIT", 1)
        _asset_rows(svc, [("db1", "a1", ".x", [])])
        store = FakeStore([
            item(path="/a.png", cls="image", fileExt="png", distance=0.1),
            item(path="/b.png", cls="image", fileExt="png", distance=0.2),
            item(path="/c.glb", cls="mesh", fileExt="glb", distance=0.3),
        ])
        _, body = call(svc, store, {"query": "site", "includeSegments": False})
        assert body["aggregations"]["str_fileext"]["buckets"] == [{"key": "png", "doc_count": 2}]
        assert body["aggregations"]["str_fileext"]["sum_other_doc_count"] == 1


class TestAssetMode:
    def test_buckets_count_assets(self, svc):
        store = _two_databases(svc)
        _, body = call(svc, store, {"query": "pump notes", "entityTypes": ["asset"], "includeSegments": False})
        assert body["hits"]["total"]["value"] == 3
        assert set(body["aggregations"]) == {"str_assettype", "str_databaseid", "list_tags"}
        assert _buckets(body, "str_assettype") == [{"key": ".txt", "doc_count": 2}, {"key": ".glb", "doc_count": 1}]
        assert _buckets(body, "str_databaseid") == [{"key": "db1", "doc_count": 2}, {"key": "db2", "doc_count": 1}]
        assert _buckets(body, "list_tags") == [
            {"key": "pump", "doc_count": 2}, {"key": "secret", "doc_count": 1}, {"key": "survey", "doc_count": 1},
        ]

    def test_an_asset_without_a_type_is_in_no_type_bucket(self, svc):
        _asset_rows(svc, [("db1", "a1", "", []), ("db1", "a2", ".glb", [])])
        store = FakeStore([item(asset="a1", distance=0.1), item(asset="a2", distance=0.2)])
        _, body = call(svc, store, {"query": "tractor", "entityTypes": ["asset"]})
        assert _buckets(body, "str_assettype") == [{"key": ".glb", "doc_count": 1}]


@pytest.mark.parametrize("entity_types", [["file"], ["asset"]])
def test_an_empty_answer_has_no_aggregations(svc, entity_types):
    _asset_rows(svc, [])
    _, body = call(svc, FakeStore([]), {"query": "tractor", "entityTypes": entity_types})
    assert body["aggregations"] == {}


@pytest.mark.usefixtures("opensearch")
def test_a_hit_the_opensearch_constraints_drop_is_counted_nowhere(svc):
    behaviour.rows(svc, ("db1", "a1"))
    store = FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])
    _, body = call(svc, store, {"query": "tractor", "tags": ["x"]})
    assert [h["_source"]["str_key"] for h in body["hits"]["hits"]] == ["a.glb"]
    assert _buckets(body, "str_fileext") == [{"key": "glb", "doc_count": 1}]
    assert _buckets(body, "str_databaseid") == [{"key": "db1", "doc_count": 1}]


def test_the_envelope_matches_the_response_model(svc):
    from models.vectorsearch import NlpSearchResponseModel

    store = _two_databases(svc)
    _, body = call(svc, store, {"query": "pump notes"})
    parsed = NlpSearchResponseModel.parse_obj(body)
    assert parsed.dict(by_alias=True)["aggregations"] == body["aggregations"]
