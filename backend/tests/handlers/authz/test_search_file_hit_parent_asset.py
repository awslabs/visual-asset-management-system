# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search file hits are checked against their parent asset's attributes.

A file document carries no assetType, so the search response processor reads each file hit's
parent asset row and builds the Casbin object from that row's assetName, tags and assetType, with
databaseId taken from the hit. This runs the real policy generation and Casbin evaluation; only
the two policy-table reads and the asset-table reads are stubbed.

search.py is loaded by path, as the search fixtures do, with ``handlers.authz`` pointing at the
real authz module that this directory's conftest makes importable.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

USER_ID = "search-user"
ROLE = "searcher"
DATABASE_ID = "db-x"


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


@pytest.fixture
def search_module():
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}

    sys.modules["handlers.authz"] = authz_module
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": [USER_ID]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_file_hit_parent", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module


class _AssetTable:
    """Asset rows by (databaseId, assetId), served through batch_get_items and get_item."""

    def __init__(self, rows, unresolved=()):
        self.rows = {(row["databaseId"], row["assetId"]): row for row in rows}
        self.unresolved = set(unresolved)
        self.batch_keys = []
        self.get_item_keys = []

    def batch_get_items(self, dynamodb_resource, table_name, keys, **kwargs):
        self.batch_keys.append([dict(key) for key in keys])
        found, pending = [], []
        for key in keys:
            marker = (key["databaseId"], key["assetId"])
            if marker in self.unresolved:
                pending.append(key)
            elif marker in self.rows:
                found.append(self.rows[marker])
        return found, pending

    def get_item(self, Key):
        self.get_item_keys.append(dict(Key))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": row} if row else {}


def _asset_row(asset_id, asset_type, database_id=DATABASE_ID, tags=None):
    return {
        "databaseId": database_id,
        "assetId": asset_id,
        "assetName": f"name-{asset_id}",
        "assetType": asset_type,
        "tags": tags or [],
    }


def _file_hit(asset_id, key="model.bin", archived=False, database_id=DATABASE_ID):
    return {
        "_index": "vams-file",
        "_id": f"{asset_id}/{key}",
        "_score": 1.0,
        "_index_type": "file",
        "_source": {
            "str_rectype": "file",
            "str_databaseid": database_id,
            "str_assetid": asset_id,
            "str_assetname": f"name-{asset_id}",
            "str_key": key,
            "bool_archived": archived,
            "list_tags": [],
        },
    }


def _asset_hit(asset_id, asset_type):
    return {
        "_index": "vams-asset",
        "_id": asset_id,
        "_score": 1.0,
        "_index_type": "asset",
        "_source": {
            "str_rectype": "asset",
            "str_databaseid": DATABASE_ID,
            "str_assetid": asset_id,
            "str_assetname": f"name-{asset_id}",
            "str_assettype": asset_type,
            "list_tags": [],
        },
    }


def _constraint(constraint_id, criteria_and, permission_type="allow", object_type="asset"):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and,
        "criteriaOr": [],
        "groupPermissions": [{"groupId": ROLE, "permission": "GET", "permissionType": permission_type}],
        "userPermissions": [],
    }


def _crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


# GET on database X, every asset in it, minus assets of type '.secret'.
DENY_SECRET_TYPE = [
    _constraint("allow-db", [_crit("databaseId", "equals", DATABASE_ID)], object_type="database"),
    _constraint("allow-assets", [_crit("databaseId", "equals", DATABASE_ID)]),
    _constraint("deny-secret", [_crit("assetType", "equals", ".secret")], permission_type="deny"),
]

# GET on database X, and on its assets of type '.glb' only.
ALLOW_GLB_TYPE = [
    _constraint("allow-db", [_crit("databaseId", "equals", DATABASE_ID)], object_type="database"),
    _constraint(
        "allow-glb",
        [_crit("databaseId", "equals", DATABASE_ID), _crit("assetType", "equals", ".glb")],
    ),
]


def _run(module, monkeypatch, constraints, table, hits):
    """Hits that survive the processor's per-hit check, by _id."""
    monkeypatch.setattr(module, "batch_get_items", table.batch_get_items)
    asset_table = MagicMock()
    asset_table.get_item.side_effect = table.get_item
    monkeypatch.setattr(module, "asset_storage_table", asset_table)

    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    request = module.SearchRequestModel(**{"from": 0, "size": 100, "aggregations": False})
    response = {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {"hits": hits, "total": {"value": len(hits), "relation": "eq"}},
    }
    with patch.object(
        authz_module.CasbinEnforcerService,
        "_read_current_user_roles_from_table",
        return_value=[{"userId": USER_ID, "roleName": ROLE}],
    ), patch.object(
        authz_module.CasbinEnforcerService,
        "_read_policies_batch_optimized",
        return_value=constraints,
    ):
        processor = module.DualIndexResponseProcessor(module.DatabaseAccessManager())
        result = processor.process_dual_search_response(
            response, request, {"tokens": [USER_ID], "roles": [ROLE], "mfaEnabled": True}
        )
    return [hit["_id"] for hit in result.dict(by_alias=True)["hits"]["hits"]]


@pytest.mark.unit
class TestSearchFileHitParentAsset:
    def test_file_of_a_denied_asset_type_is_withheld(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("secret-asset", ".secret"), _asset_row("plain-asset", ".glb")])
        hits = [_file_hit("secret-asset"), _file_hit("plain-asset")]

        returned = _run(search_module, monkeypatch, DENY_SECRET_TYPE, table, hits)

        assert returned == ["plain-asset/model.bin"]

    def test_asset_hit_of_a_denied_type_is_still_withheld(self, search_module, monkeypatch):
        # Positive control for the policy: the same deny applies to the asset document itself.
        table = _AssetTable([])
        hits = [_asset_hit("secret-asset", ".secret"), _asset_hit("plain-asset", ".glb")]

        returned = _run(search_module, monkeypatch, DENY_SECRET_TYPE, table, hits)

        assert returned == ["plain-asset"]
        assert table.batch_keys == []

    def test_type_scoped_allow_returns_files_of_allowed_assets(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb"), _asset_row("obj-asset", ".obj")])
        hits = [_file_hit("glb-asset"), _file_hit("obj-asset")]

        returned = _run(search_module, monkeypatch, ALLOW_GLB_TYPE, table, hits)

        assert returned == ["glb-asset/model.bin"]

    def test_archived_file_of_a_live_asset_uses_the_live_row(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb")])
        hits = [_file_hit("glb-asset", archived=True)]

        returned = _run(search_module, monkeypatch, ALLOW_GLB_TYPE, table, hits)

        assert returned == ["glb-asset/model.bin"]
        # The live row answered, so the archived partition is never read.
        assert table.batch_keys == [[{"databaseId": DATABASE_ID, "assetId": "glb-asset"}]]

    def test_file_of_an_archived_asset_uses_the_archived_row(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb", database_id=f"{DATABASE_ID}#deleted")])
        hits = [_file_hit("glb-asset", archived=True)]

        returned = _run(search_module, monkeypatch, ALLOW_GLB_TYPE, table, hits)

        assert returned == ["glb-asset/model.bin"]
        assert table.batch_keys[1] == [{"databaseId": f"{DATABASE_ID}#deleted", "assetId": "glb-asset"}]

    def test_file_without_a_parent_row_is_withheld(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb")])
        hits = [_file_hit("glb-asset"), _file_hit("missing-asset")]

        returned = _run(search_module, monkeypatch, DENY_SECRET_TYPE, table, hits)

        assert returned == ["glb-asset/model.bin"]

    def test_file_with_an_empty_asset_id_is_withheld_and_not_read(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb")])
        hits = [_file_hit("glb-asset"), _file_hit("")]

        returned = _run(search_module, monkeypatch, DENY_SECRET_TYPE, table, hits)

        assert returned == ["glb-asset/model.bin"]
        assert all(key["assetId"] for batch in table.batch_keys for key in batch)

    def test_unresolved_parent_key_is_read_individually(self, search_module, monkeypatch):
        table = _AssetTable(
            [_asset_row("secret-asset", ".secret"), _asset_row("plain-asset", ".glb")],
            unresolved=[(DATABASE_ID, "secret-asset"), (DATABASE_ID, "plain-asset")],
        )
        hits = [_file_hit("secret-asset"), _file_hit("plain-asset")]

        returned = _run(search_module, monkeypatch, DENY_SECRET_TYPE, table, hits)

        assert returned == ["plain-asset/model.bin"]
        assert {key["assetId"] for key in table.get_item_keys} == {"secret-asset", "plain-asset"}

    def test_parent_rows_are_read_once_per_asset(self, search_module, monkeypatch):
        table = _AssetTable([_asset_row("glb-asset", ".glb")])
        hits = [_file_hit("glb-asset", key="a.bin"), _file_hit("glb-asset", key="b.bin")]

        returned = _run(search_module, monkeypatch, ALLOW_GLB_TYPE, table, hits)

        assert returned == ["glb-asset/a.bin", "glb-asset/b.bin"]
        assert table.batch_keys == [[{"databaseId": DATABASE_ID, "assetId": "glb-asset"}]]


class _BatchResource:
    """A DynamoDB resource whose batch_get_item answers from `rows` and leaves `throttled` keys
    unprocessed for the first `throttle_rounds` calls that carry them."""

    def __init__(self, table_name, rows, throttled=(), throttle_rounds=0):
        self.table_name = table_name
        self.rows = {(row["databaseId"], row["assetId"]): row for row in rows}
        self.throttled = set(throttled)
        self.throttle_rounds = throttle_rounds
        self.calls = []

    def batch_get_item(self, RequestItems):
        keys = RequestItems[self.table_name]["Keys"]
        self.calls.append([dict(key) for key in keys])
        throttle = self.throttle_rounds > 0
        if throttle:
            self.throttle_rounds -= 1
        found, pending = [], []
        for key in keys:
            marker = (key["databaseId"], key["assetId"])
            if throttle and marker in self.throttled:
                pending.append(key)
            elif marker in self.rows:
                found.append(self.rows[marker])
        response = {"Responses": {self.table_name: found}}
        if pending:
            response["UnprocessedKeys"] = {self.table_name: {"Keys": pending}}
        return response


def _key(asset_id):
    return {"databaseId": DATABASE_ID, "assetId": asset_id}


class TestSearchBatchGetItems:
    """search.py's own chunked BatchGetItem reader, which release/2.6.6 carries in place of the
    shared common.dynamodb helper."""

    def test_keys_are_sent_in_chunks_of_one_hundred(self, search_module, monkeypatch):
        monkeypatch.setattr(search_module.time, "sleep", MagicMock())
        rows = [_asset_row(f"a{n}", ".glb") for n in range(250)]
        resource = _BatchResource("assets", rows)

        found, unresolved = search_module.batch_get_items(resource, "assets", [_key(f"a{n}") for n in range(250)])

        assert [len(call) for call in resource.calls] == [100, 100, 50]
        assert len(found) == 250 and unresolved == []

    def test_a_repeated_key_is_sent_once(self, search_module, monkeypatch):
        monkeypatch.setattr(search_module.time, "sleep", MagicMock())
        resource = _BatchResource("assets", [_asset_row("a1", ".glb")])

        found, unresolved = search_module.batch_get_items(resource, "assets", [_key("a1"), _key("a1")])

        assert resource.calls == [[_key("a1")]]
        assert [row["assetId"] for row in found] == ["a1"] and unresolved == []

    def test_unprocessed_keys_are_re_requested(self, search_module, monkeypatch):
        sleep = MagicMock()
        monkeypatch.setattr(search_module.time, "sleep", sleep)
        resource = _BatchResource(
            "assets", [_asset_row("a1", ".glb"), _asset_row("a2", ".glb")], throttled=[(DATABASE_ID, "a2")],
            throttle_rounds=1,
        )

        found, unresolved = search_module.batch_get_items(resource, "assets", [_key("a1"), _key("a2")])

        assert resource.calls == [[_key("a1"), _key("a2")], [_key("a2")]]
        assert sorted(row["assetId"] for row in found) == ["a1", "a2"] and unresolved == []
        assert sleep.call_count == 1

    def test_keys_still_unprocessed_after_the_retries_are_returned_unresolved(self, search_module, monkeypatch):
        sleep = MagicMock()
        monkeypatch.setattr(search_module.time, "sleep", sleep)
        resource = _BatchResource(
            "assets", [_asset_row("a1", ".glb"), _asset_row("a2", ".glb")], throttled=[(DATABASE_ID, "a2")],
            throttle_rounds=100,
        )

        found, unresolved = search_module.batch_get_items(resource, "assets", [_key("a1"), _key("a2")])

        assert [row["assetId"] for row in found] == ["a1"]
        assert unresolved == [_key("a2")]
        assert len(resource.calls) == search_module.BATCH_GET_MAX_RETRIES + 1
        assert sleep.call_count == search_module.BATCH_GET_MAX_RETRIES
