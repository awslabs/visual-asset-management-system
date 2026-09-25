# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp behaviour against a fake VectorStore: planning, merge, collapse, intent, authz, envelope.

The handler is loaded by path (`vectorsearch_support.load_handler`) so the algorithm under test is the
real module; only the auth surface, the embedding call and the database-access manager are replaced.
"""

import importlib.util
import json
import os
import sys
import types
from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.osVectorSearch import vectorsearch_support as support

MODEL = os.environ["EMBEDDING_MODEL_ID"]
DIMENSIONS = int(os.environ["EMBEDDING_DIMENSIONS"])
_DATABASE_ACCESS = os.path.join(support._BACKEND, "common", "databaseAccess.py")


def _bind_database_access():
    """The real ``common.databaseAccess`` under the mock ``common`` package, so the handler's import binds
    and the live-database scoping test below runs the real scan-and-enforce loop."""
    current = sys.modules.get("common.databaseAccess")
    bound_file = os.path.abspath(getattr(current, "__file__", "") or "")
    if current is not None and bound_file == os.path.abspath(_DATABASE_ACCESS):
        return
    os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-database-table")
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("common.databaseAccess", os.path.abspath(_DATABASE_ACCESS))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    sys.modules["common.databaseAccess"] = module


def _load_service():
    """The handler loaded by path with ``handlers.auth`` / ``handlers.authz`` stubbed for the duration of
    the load; other suites' conftests re-register those names, so the stubs are installed per load. The
    real ``common.databaseAccess`` is bound under the same stubs, since it imports ``CasbinEnforcer`` too."""
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["t"], "roles": []})
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.auth"], sys.modules["handlers.authz"] = auth_stub, authz_stub
    try:
        _bind_database_access()
        return support.load_handler("vectorSearchService")
    finally:
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_DISABLED", "true")
    module = _load_service()
    module.request_to_claims = MagicMock(return_value={"tokens": ["t"], "roles": []})
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    module.CasbinEnforcer = MagicMock(return_value=enforcer)
    module.embed_text = MagicMock(return_value=[0.0] * DIMENSIONS)
    access = MagicMock()
    access.get_accessible_databases_with_count.return_value = (["db1"], 2)
    module.DatabaseAccessManager = MagicMock(return_value=access)
    module.enforcer, module.access = enforcer, access
    return module


@dataclass
class Hit:
    """The two attributes of a store hit the handler reads (the ``VectorHit`` shape)."""

    item: Dict[str, Any]
    distance: float


class FakeStore:
    """Answers ``search`` from a list of (item, distance) pairs by applying the equality filters."""

    def __init__(self, items):
        self.items, self.calls = items, []

    def search(self, vector, *, top_k, filters):
        self.calls.append(dict(filters))
        hits = [Hit(item=i, distance=d) for i, d in self.items if all(i.get(k) == v for k, v in filters.items())]
        return sorted(hits, key=lambda h: h.distance)[:top_k]


def item(db="db1", asset="a1", path="f.glb", version="v1", distance=0.2, cls="mesh", **extra):
    base = {
        "databaseId": db, "assetId": asset, "filePath": path, "versionId": version, "isLatest": "true",
        "isArchived": "false", "fileClass": cls, "fileExt": "glb", "embeddingModelId": MODEL,
        "segmentKey": "", "segmentKind": "none", "segmentLabel": "", "segmentStartMs": None, "segmentEndMs": None,
        "fileSize": 10, "indexedAt": "2026-09-13T00:00:00Z", "sourceModalities": ["file-identity"],
    }
    base.update(extra)
    return base, distance


def chunk(key, distance, **extra):
    return item(distance=distance, segmentKey=key, segmentKind="textChunk", segmentLabel=key, **extra)


def event(body, path="/search/nlp", method="POST"):
    return {"requestContext": {"http": {"path": path, "method": method}}, "body": json.dumps(body)}


def rows(svc, *pairs):
    svc._load_asset_rows = MagicMock(
        return_value={(db, a): {"databaseId": db, "assetId": a, "assetName": a, "assetType": "x", "tags": []} for db, a in pairs}
    )


def call(svc, store, body):
    svc._store = MagicMock(return_value=store)
    resp = svc.lambda_handler(event(body), None)
    return resp["statusCode"], json.loads(resp["body"])


BREADTH_DB1 = {"databaseId": "db1", "isLatest": "true", "isArchived": "false", "embeddingModelId": MODEL, "segmentKind": "none"}


class TestAuthorization:
    def test_tier1_denies_without_tokens_before_any_search(self, svc):
        svc.request_to_claims.return_value = {"tokens": []}
        store = FakeStore([item()])
        status, _ = call(svc, store, {"query": "tractor"})
        assert status == 403
        assert store.calls == []
        assert not svc.embed_text.called

    def test_tier1_denies_when_enforce_api_fails(self, svc):
        svc.enforcer.enforceAPI.return_value = False
        store = FakeStore([item()])
        status, _ = call(svc, store, {"query": "tractor"})
        assert status == 403 and store.calls == []

    def test_per_hit_casbin_drops_denied_and_rowless_assets(self, svc):
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        svc.enforcer.enforce.side_effect = lambda obj, action: obj["object__type"] == "asset" and obj["assetName"] != "a2"
        store = FakeStore([item(asset="a1", distance=0.1), item(asset="a2", distance=0.2), item(asset="a3", distance=0.3)])
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 200
        assert [h["_source"]["str_assetid"] for h in body["hits"]["hits"]] == ["a1"]
        assert body["hits"]["total"]["value"] == 1

    def test_archived_items_read_the_deleted_partition(self, svc):
        def batch_get_item(RequestItems):
            keys = RequestItems[svc.asset_storage_table_name]["Keys"]
            found = [
                {"databaseId": k["databaseId"], "assetId": k["assetId"], "assetName": "gone", "assetType": "x", "tags": []}
                for k in keys if k["databaseId"] == "db1#deleted"
            ]
            return {"Responses": {svc.asset_storage_table_name: found}}

        svc.dynamodb.batch_get_item = MagicMock(side_effect=batch_get_item)
        store = FakeStore([item(isArchived="true")])
        status, body = call(svc, store, {"query": "tractor", "includeArchived": True})
        assert status == 200
        # Database scoping is over the live database rows whatever includeArchived says, as /search does.
        assert svc.access.get_accessible_databases_with_count.call_args.kwargs["show_deleted"] is False
        assert len(store.calls) >= 1
        assert all("isArchived" not in c for c in store.calls)
        assert len(body["hits"]["hits"]) == 1
        assert body["hits"]["hits"][0]["_source"]["bool_archived"] is True
        assert body["hits"]["hits"][0]["_source"]["str_assetname"] == "gone"
        assert svc.dynamodb.batch_get_item.call_count <= 2

    def test_include_archived_scopes_to_the_live_databases_the_caller_may_read(self, svc, monkeypatch):
        """Through the REAL ``DatabaseAccessManager`` over a database table holding two live rows and one
        ``#deleted`` row, with Casbin granting the database ``db1`` only: ``includeArchived`` still scans
        the live rows, so the plan targets ``db1`` alone (never the unpartitioned ``None`` an all-access
        caller gets), the archived ``db1`` item is a hit, and ``db2``'s item is never searched."""
        database_access = sys.modules["common.databaseAccess"]
        table_rows = [
            {"databaseId": {"S": "db1"}, "description": {"S": "live"}},
            {"databaseId": {"S": "db2"}, "description": {"S": "live"}},
            {"databaseId": {"S": "db3#deleted"}, "description": {"S": "deleted"}},
        ]
        scans = []

        def paginate(**kwargs):
            scans.append(kwargs)
            operator = kwargs["ScanFilter"]["databaseId"]["ComparisonOperator"]
            deleted = [row for row in table_rows if "#deleted" in row["databaseId"]["S"]]
            live = [row for row in table_rows if "#deleted" not in row["databaseId"]["S"]]
            yield {"Items": deleted if operator == "CONTAINS" else live}

        paginator = MagicMock()
        paginator.paginate.side_effect = paginate
        monkeypatch.setattr(database_access.dynamodb_client, "get_paginator", MagicMock(return_value=paginator))

        def enforce(obj, action):
            if obj["object__type"] == "database":
                return obj["databaseId"] == "db1"
            return obj["object__type"] == "asset"

        access_enforcer = MagicMock()
        access_enforcer.enforce.side_effect = enforce
        monkeypatch.setattr(database_access, "CasbinEnforcer", MagicMock(return_value=access_enforcer))
        svc.DatabaseAccessManager = database_access.DatabaseAccessManager
        svc.enforcer.enforce.side_effect = enforce
        rows(svc, ("db1", "a1"), ("db2", "a2"))
        store = FakeStore([item(db="db1", asset="a1", isArchived="true", distance=0.1),
                           item(db="db2", asset="a2", path="other.glb", distance=0.2)])

        status, body = call(svc, store, {"query": "tractor", "includeArchived": True})

        assert status == 200
        assert [s["ScanFilter"]["databaseId"]["ComparisonOperator"] for s in scans] == ["NOT_CONTAINS"]
        assert [h["_source"]["str_databaseid"] for h in body["hits"]["hits"]] == ["db1"]
        assert body["hits"]["hits"][0]["_source"]["bool_archived"] is True
        assert body["nlp"]["databasesSearched"] == 1
        assert store.calls
        assert {c.get("databaseId") for c in store.calls} == {"db1"}
        assert not any("#deleted" in str(c) for c in store.calls)


class TestPlanning:
    def test_per_target_filters_in_index_order(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 200
        assert len(store.calls) >= 1
        assert store.calls[0] == BREADTH_DB1
        assert list(store.calls[0]) == ["databaseId", "isLatest", "isArchived", "embeddingModelId", "segmentKind"]
        assert body["nlp"]["databasesSearched"] == 1
        assert body["hits"]["total"]["relation"] == "eq"

    def test_all_access_caller_gets_one_unpartitioned_pair(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 2)
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        _, body = call(svc, store, {"query": "tractor"})
        assert len(store.calls) == 2
        assert all("databaseId" not in c for c in store.calls)
        assert body["nlp"]["databasesSearched"] == 2

    def test_partial_access_caller_gets_one_pair_per_database(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 3)
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        _, body = call(svc, store, {"query": "tractor"})
        assert {c["databaseId"] for c in store.calls} == {"db1", "db2"}
        assert body["nlp"]["databasesSearched"] == 2

    def test_named_databases_are_intersected_with_access(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 2)
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        _, body = call(svc, store, {"query": "tractor", "databaseIds": ["db2", "db9", "db2"]})
        assert {c["databaseId"] for c in store.calls} == {"db2"}
        assert body["nlp"]["databasesSearched"] == 1

    def test_segment_switch(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        call(svc, store, {"query": "tractor", "includeSegments": False})
        assert len(store.calls) == 1 and store.calls[0]["segmentKind"] == "none"
        store = FakeStore([item()])
        call(svc, store, {"query": "tractor", "includeSegments": True})
        assert len(store.calls) == 2
        assert store.calls[0]["segmentKind"] == "none" and "segmentKind" not in store.calls[1]

    def test_single_value_class_and_extension_push_down(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item()])
        call(svc, store, {"query": "tractor", "fileClasses": ["Mesh"], "fileExtensions": [".GLB"]})
        assert len(store.calls) >= 1
        assert all(c["fileClass"] == "mesh" and c["fileExt"] == "glb" for c in store.calls)
        assert list(store.calls[0]) == ["databaseId", "isLatest", "isArchived", "fileClass", "fileExt", "embeddingModelId", "segmentKind"]

    def test_multi_value_class_and_extension_filter_after_the_merge(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([
            item(path="a.glb", cls="mesh", distance=0.1),
            item(path="b.mp4", cls="video", distance=0.2, fileExt="mp4"),
            item(path="c.png", cls="image", distance=0.3, fileExt="png"),
        ])
        _, body = call(svc, store, {"query": "tractor", "fileClasses": ["mesh", "video"], "fileExtensions": ["glb", "mp4"]})
        assert len(store.calls) >= 1
        assert all("fileClass" not in c and "fileExt" not in c for c in store.calls)
        assert [h["_source"]["str_key"] for h in body["hits"]["hits"]] == ["a.glb", "b.mp4"]
        assert body["nlp"]["candidatesEvaluated"] == 2

    def test_explicit_classes_suppress_intent_detection(self, svc):
        rows(svc, ("db1", "a1"))
        _, body = call(svc, FakeStore([item()]), {"query": "video of the crane", "fileClasses": ["mesh"]})
        assert body["nlp"]["classIntent"] == []


class TestCollapseAndOrder:
    def test_segment_wins_the_collapse(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(distance=0.3), chunk("c000001", 0.1), chunk("c000002", 0.2)])
        _, body = call(svc, store, {"query": "tractor"})
        hits = body["hits"]["hits"]
        assert len(hits) == 1
        vector = hits[0]["_vector"]
        assert vector["distance"] == 0.1 and vector["segmentHits"] == 2
        assert vector["bestSegment"]["segmentKey"] == "c000001" and vector["bestSegment"]["segmentKind"] == "textChunk"
        assert body["nlp"]["itemsCollapsed"] == 2 and body["nlp"]["candidatesEvaluated"] == 3

    def test_whole_file_wins_the_collapse(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(distance=0.1), chunk("c000001", 0.2)])
        _, body = call(svc, store, {"query": "tractor"})
        vector = body["hits"]["hits"][0]["_vector"]
        assert vector["distance"] == 0.1 and vector["segmentHits"] == 1 and vector["bestSegment"] is None

    def test_intent_partition_orders_named_classes_first(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(path="p.png", cls="image", distance=0.1, fileExt="png"), item(path="v.mp4", cls="video", distance=0.2, fileExt="mp4")])
        _, body = call(svc, store, {"query": "video of the crane"})
        assert [h["_vector"]["fileClass"] for h in body["hits"]["hits"]] == ["video", "image"]
        assert body["nlp"]["classIntent"] == ["video"]
        assert body["hits"]["max_score"] == body["hits"]["hits"][0]["_score"]

    def test_size_slices_after_authorization_and_total_counts_before(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(path=f"f{i}.glb", distance=0.1 + i / 100) for i in range(5)])
        _, body = call(svc, store, {"query": "tractor", "size": 2, "includeSegments": False})
        assert len(body["hits"]["hits"]) == 2
        assert body["hits"]["total"]["value"] == 4 and body["aggregationTotal"] == 4
        assert body["nlp"]["truncated"] is True


class TestTruncation:
    def test_full_breadth_window_marks_the_answer_truncated(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])
        _, body = call(svc, store, {"query": "tractor", "size": 1, "includeSegments": False})
        assert body["nlp"]["truncated"] is True and body["hits"]["total"]["relation"] == "gte"
        assert {w["code"] for w in body["warnings"]} == {"truncated:window"}

    def test_full_depth_window_alone_only_warns(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(distance=0.3), chunk("c000001", 0.1), chunk("c000002", 0.2)])
        _, body = call(svc, store, {"query": "tractor", "size": 1})
        assert body["nlp"]["truncated"] is False and body["hits"]["total"]["relation"] == "eq"
        assert {w["code"] for w in body["warnings"]} == {"segments:window_full"}

    def test_target_list_is_cut_at_max_targets(self, svc):
        accessible = [f"db{i}" for i in range(201)]
        svc.access.get_accessible_databases_with_count.return_value = (accessible, 300)
        rows(svc, ("db0", "a1"))
        store = FakeStore([item(db="db0")])
        _, body = call(svc, store, {"query": "tractor", "includeSegments": False})
        assert len(store.calls) == svc.MAX_TARGETS == 200
        assert body["nlp"]["databasesSearched"] == 200 and body["nlp"]["truncated"] is True
        assert {w["code"] for w in body["warnings"]} == {"truncated:targets"}


class TestErrorsAndEnvelope:
    def test_index_not_ready_is_503(self, svc):
        store = MagicMock()
        store.search.side_effect = svc.VectorIndexNotReady("x")
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 503 and body == {"message": "Vector index is being built"}

    def test_embedding_failure_is_a_general_error_without_the_query(self, svc):
        svc.embed_text.side_effect = svc.EmbeddingModelError("boom", code="AccessDeniedException")
        status, body = call(svc, FakeStore([]), {"query": "secret tractor"})
        assert status == 400
        assert "AccessDeniedException" in body["message"] and "secret tractor" not in body["message"]

    def test_invalid_request_is_400_without_echo(self, svc):
        status, body = call(svc, FakeStore([]), {"query": "x", "entityTypes": ["folder-zzz"]})
        assert status == 400 and "folder-zzz" not in body["message"]
        resp = svc.lambda_handler({"requestContext": {"http": {"path": "/search/nlp", "method": "POST"}}, "body": "{not json"}, None)
        assert resp["statusCode"] == 400

    def test_other_routes_are_not_served(self, svc):
        resp = svc.lambda_handler(event({"query": "x"}, path="/search/simple"), None)
        assert resp["statusCode"] == 400
        assert "/search/simple" not in resp["body"]

    def test_envelope_contract(self, svc):
        rows(svc, ("db1", "a1"))
        _, body = call(svc, FakeStore([item(distance=0.2, previewFileKey="p.png")]), {"query": "tractor"})
        assert set(body) == {"took", "timed_out", "_shards", "hits", "aggregations", "aggregationTotal", "nlp", "warnings"}
        assert set(body["nlp"]) == {"query", "embeddingModelId", "databasesSearched", "candidatesEvaluated", "itemsCollapsed", "classIntent", "truncated"}
        hit = body["hits"]["hits"][0]
        assert set(hit) == {"_index", "_id", "_score", "_index_type", "_source", "_vector"}
        assert set(hit["_source"]) <= {
            "str_rectype", "str_databaseid", "str_assetid", "str_key", "str_fileext", "str_assetname", "str_assettype",
            "list_tags", "bool_archived", "str_s3_version_id", "num_filesize", "str_previewfilekey",
        }
        assert set(hit["_vector"]) == {"distance", "embeddingModelId", "sourceModalities", "indexedAt", "fileClass", "segmentHits", "bestSegment"}
        assert hit["_id"] == "db1#a1#f.glb#v1" and hit["_index"] == "vams-vectors" and hit["_index_type"] == "file"
        assert hit["_score"] == pytest.approx(0.8)
        assert hit["_source"]["str_previewfilekey"] == "p.png" and hit["_source"]["str_s3_version_id"] == "v1"

    def test_asset_mode_collapses_files_to_their_asset(self, svc):
        rows(svc, ("db1", "a1"))
        store = FakeStore([item(path="a.glb", distance=0.1), item(path="b.glb", distance=0.2)])
        _, body = call(svc, store, {"query": "tractor", "entityTypes": ["asset"]})
        hits = body["hits"]["hits"]
        assert len(hits) == 1
        assert hits[0]["_index_type"] == "asset" and hits[0]["_id"] == "db1#a1"
        assert hits[0]["_source"]["str_rectype"] == "asset" and "str_key" not in hits[0]["_source"]
        assert hits[0]["_vector"]["distance"] == 0.1

    def test_opensearch_only_fields_are_ignored_when_opensearch_is_off(self, svc):
        rows(svc, ("db1", "a1"))
        svc._opensearch_step = MagicMock()
        _, body = call(svc, FakeStore([item()]), {"query": "tractor", "tags": ["x"]})
        codes = {w["code"]: w["message"] for w in body["warnings"]}
        assert "opensearch:fields_ignored" in codes and "tags" in codes["opensearch:fields_ignored"]
        assert not svc._opensearch_step.called

    def test_no_accessible_database_is_an_empty_answer(self, svc):
        svc.access.get_accessible_databases_with_count.return_value = ([], 2)
        store = FakeStore([item()])
        status, body = call(svc, store, {"query": "tractor"})
        assert status == 200 and body["hits"]["hits"] == [] and body["hits"]["total"]["value"] == 0
        assert {w["code"] for w in body["warnings"]} == {"databases:none_accessible"}
        assert not svc.embed_text.called and store.calls == []

