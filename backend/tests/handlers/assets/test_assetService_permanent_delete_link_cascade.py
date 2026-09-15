# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Permanently deleting an asset removes every asset link that names it, at either end.

The cascade already existed, and it had never removed a row. It queried the V2 links table with the
V1 key schema -- `Key('assetIdFrom')`, `IndexName='AssetIdToGSI'`, a delete key of
`{assetIdFrom, assetIdTo}` -- where the V2 table is keyed by `assetLinkId` and indexes its two ends on
`fromAssetGSI` / `toAssetGSI` under `fromAssetDatabaseId:fromAssetId` / `toAssetDatabaseId:toAssetId`.
Every query therefore failed DynamoDB's key-schema validation and was swallowed by a bare
`except Exception` at warning level, so an asset's permanent deletion reported success while all of
its links survived -- and the surviving links could then not be deleted through the API either.

These tests drive the extracted helper against table stubs that answer ONLY the V2 shape: a
`RoutedPager` keyed on `IndexName` serves the two GSIs and raises for any other index name, so the
V1 query cannot pass silently, and the delete key is asserted to be `assetLinkId` alone.
"""

import importlib.util
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

from backend.tests.pagingStub import Pager, RoutedPager

for _name, _value in (
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table"),
    ("ASSET_STORAGE_TABLE_NAME", "test-asset-table"),
    ("DATABASE_STORAGE_TABLE_NAME", "test-db-table"),
    ("S3_ASSET_AUXILIARY_BUCKET", "test-aux-bucket"),
    ("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subs-table"),
    ("SEND_EMAIL_FUNCTION_NAME", "test-email-fn"),
    ("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table"),
    ("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table"),
    ("ASSET_UPLOAD_TABLE_NAME", "test-upload-table"),
    ("ASSET_LINKS_STORAGE_TABLE_NAME", "test-links-table"),
    ("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-links-meta-table"),
    ("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-meta-table"),
    ("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table"),
    ("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table"),
    ("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-file-versions-table"),
    ("ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE_NAME", "test-file-meta-versions-table"),
    ("COMMENT_STORAGE_TABLE_NAME", "test-comment-table"),
):
    os.environ.setdefault(_name, _value)

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)


def _load():
    """Load the real assetService module by file path with its handler imports stubbed.

    The same shape the sibling assetService suites use: the module imports handler siblings
    (`handlers.assets.assetCount`, `handlers.assets.assetFiles`, `handlers.authz`, `handlers.auth`)
    that need AWS bootstrap, so they are stubbed for the load and restored afterwards, and boto3 is
    patched so no client is built at import.
    """
    stub_names = ("handlers.assets.assetCount", "handlers.assets.assetFiles", "handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    count_stub = types.ModuleType("handlers.assets.assetCount"); count_stub.update_asset_count = MagicMock()
    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.aux_bucket_asset_file_base = lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    authz_stub = types.ModuleType("handlers.authz"); authz_stub.CasbinEnforcer = MagicMock()
    auth_stub = types.ModuleType("handlers.auth"); auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules.update({"handlers.assets.assetCount": count_stub, "handlers.assets.assetFiles": files_stub,
                        "handlers.authz": authz_stub, "handlers.auth": auth_stub})
    dynamodb_mod = sys.modules.get("common.dynamodb")
    added = []
    if dynamodb_mod is not None and not hasattr(dynamodb_mod, "validate_pagination_info"):
        dynamodb_mod.validate_pagination_info = MagicMock(); added.append("validate_pagination_info")
    try:
        with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(
                "assetService_link_cascade_under_test", os.path.abspath(_ASSET_SERVICE_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None: sys.modules[name] = mod
            else: sys.modules.pop(name, None)
        for attr in added:
            delattr(dynamodb_mod, attr)
    return module


_cached_module = None


def _svc():
    """The loaded module, built on first use.

    Loading must wait for the autouse mock-import fixture: `common` is a MagicMock package whose
    submodules (`common.assetHistory` among them) are registered per test, not at collection, so a
    module-level load raises before the first test runs.
    """
    global _cached_module
    if _cached_module is None:
        _cached_module = _load()
    return _cached_module

DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
ASSET_KEY = f"{DATABASE_ID}:{ASSET_ID}"


def _link(link_id, from_key, to_key):
    from_db, from_asset = from_key.split(":", 1)
    to_db, to_asset = to_key.split(":", 1)
    return {
        "assetLinkId": link_id,
        "fromAssetDatabaseId:fromAssetId": from_key,
        "toAssetDatabaseId:toAssetId": to_key,
        "fromAssetDatabaseId": from_db, "fromAssetId": from_asset,
        "toAssetDatabaseId": to_db, "toAssetId": to_asset,
    }


def _run(from_pages, to_pages):
    """Run the cascade with the two GSIs answering the given pages; return (table, metadata mock, deleted)."""
    from_pager = Pager(*from_pages, name="fromAssetGSI")
    to_pager = Pager(*to_pages, name="toAssetGSI")
    table = MagicMock(name="asset_links_table")
    table.query.side_effect = RoutedPager(on="IndexName", fromAssetGSI=from_pager, toAssetGSI=to_pager)
    deleted = {"dynamodb_tables": []}
    with patch.object(_svc(), "asset_links_table", table), \
            patch.object(_svc(), "asset_links_table_name", "links-table"), \
            patch.object(_svc(), "delete_asset_link_metadata_for_permanent_deletion") as meta:
        _svc()._delete_asset_links_for_permanent_deletion(DATABASE_ID, ASSET_ID, deleted)
    return table, meta, deleted, from_pager, to_pager


@pytest.mark.unit
class TestPermanentDeleteRemovesEveryLink:
    def test_links_at_both_ends_are_deleted_by_link_id(self):
        table, meta, deleted, *_ = _run(
            from_pages=[{"Items": [_link("l-out", ASSET_KEY, "db-2:other-a")]}],
            to_pages=[{"Items": [_link("l-in", "db-3:other-b", ASSET_KEY)]}],
        )
        deleted_keys = [c.kwargs["Key"] for c in table.delete_item.call_args_list]
        assert deleted_keys, "no link row was deleted at all"
        # The V2 primary key, and nothing else -- a V1 {assetIdFrom, assetIdTo} key would have
        # failed validation against this table for the life of the previous cascade.
        assert {k["assetLinkId"] for k in deleted_keys} >= {"l-in", "l-out"}
        assert all(set(k) == {"assetLinkId"} for k in deleted_keys)
        assert {c.args[0] for c in meta.call_args_list} >= {"l-in", "l-out"}
        assert {"links-table (assetLinkId=l-in)", "links-table (assetLinkId=l-out)"} <= set(deleted["dynamodb_tables"])

    def test_metadata_is_removed_before_each_link_row(self):
        order = []
        table, meta, deleted, *_ = _run(
            from_pages=[{"Items": [_link("l-1", ASSET_KEY, "db-2:x")]}], to_pages=[{"Items": []}])
        # Re-run with recording side effects to pin the order per link.
        from_pager = Pager({"Items": [_link("l-1", ASSET_KEY, "db-2:x")]}, name="fromAssetGSI")
        to_pager = Pager({"Items": []}, name="toAssetGSI")
        table = MagicMock(); table.query.side_effect = RoutedPager(on="IndexName", fromAssetGSI=from_pager, toAssetGSI=to_pager)
        table.delete_item.side_effect = lambda **kw: order.append(("link", kw["Key"]["assetLinkId"]))
        with patch.object(_svc(), "asset_links_table", table), \
                patch.object(_svc(), "asset_links_table_name", "links-table"), \
                patch.object(_svc(), "delete_asset_link_metadata_for_permanent_deletion",
                             side_effect=lambda lid: order.append(("meta", lid))):
            _svc()._delete_asset_links_for_permanent_deletion(DATABASE_ID, ASSET_ID, {"dynamodb_tables": []})
        assert order == [("meta", "l-1"), ("link", "l-1")]

    def test_a_link_seen_on_both_indexes_is_deleted_once(self):
        # A self-link (or the same row surfacing on both GSIs) must not be deleted twice or counted twice.
        table, meta, deleted, *_ = _run(
            from_pages=[{"Items": [_link("l-self", ASSET_KEY, ASSET_KEY)]}],
            to_pages=[{"Items": [_link("l-self", ASSET_KEY, ASSET_KEY)]}],
        )
        deleted_ids = [c.kwargs["Key"]["assetLinkId"] for c in table.delete_item.call_args_list]
        assert "l-self" in deleted_ids
        # De-duplication: the row that surfaced on both indexes goes at most once.
        assert table.delete_item.call_count <= 1
        assert meta.call_count <= 1
        assert len(deleted["dynamodb_tables"]) <= 1

    def test_both_indexes_are_paged_to_exhaustion(self):
        # An asset with many links must not keep the ones past the first page. Two pages per index.
        table, meta, deleted, from_pager, to_pager = _run(
            from_pages=[
                {"Items": [_link("f-1", ASSET_KEY, "db-2:a")], "LastEvaluatedKey": {"assetLinkId": "f-1"}},
                {"Items": [_link("f-2", ASSET_KEY, "db-2:b")]},
            ],
            to_pages=[
                {"Items": [_link("t-1", "db-3:c", ASSET_KEY)], "LastEvaluatedKey": {"assetLinkId": "t-1"}},
                {"Items": [_link("t-2", "db-3:d", ASSET_KEY)]},
            ],
        )
        from_pager.assert_paged_to_exhaustion()
        to_pager.assert_paged_to_exhaustion()
        deleted_ids = {c.kwargs["Key"]["assetLinkId"] for c in table.delete_item.call_args_list}
        assert deleted_ids >= {"f-1", "f-2", "t-1", "t-2"}, "a link past the first page was kept"

    def test_no_links_deletes_nothing(self):
        # CONTROL for the assertions above: an asset with no links must produce no deletes at all.
        table, meta, deleted, *_ = _run(from_pages=[{"Items": []}], to_pages=[{"Items": []}])
        table.delete_item.assert_not_called()
        meta.assert_not_called()
        assert deleted["dynamodb_tables"] == []

    def test_one_failing_link_does_not_stop_the_others(self):
        # The asset row is already gone by this point, so a single bad link is logged and skipped
        # rather than aborting the deletion -- but the OTHER links still go.
        from_pager = Pager({"Items": [_link("bad", ASSET_KEY, "db-2:a"), _link("good", ASSET_KEY, "db-2:b")]}, name="fromAssetGSI")
        to_pager = Pager({"Items": []}, name="toAssetGSI")
        table = MagicMock(); table.query.side_effect = RoutedPager(on="IndexName", fromAssetGSI=from_pager, toAssetGSI=to_pager)

        def delete(**kw):
            if kw["Key"]["assetLinkId"] == "bad":
                raise RuntimeError("delete blew up")
        table.delete_item.side_effect = delete
        deleted = {"dynamodb_tables": []}
        with patch.object(_svc(), "asset_links_table", table), \
                patch.object(_svc(), "asset_links_table_name", "links-table"), \
                patch.object(_svc(), "delete_asset_link_metadata_for_permanent_deletion"):
            _svc()._delete_asset_links_for_permanent_deletion(DATABASE_ID, ASSET_ID, deleted)
        attempted = {c.kwargs["Key"]["assetLinkId"] for c in table.delete_item.call_args_list}
        assert "good" in attempted, "the failure on 'bad' stopped the cascade"
        assert deleted["dynamodb_tables"] == ["links-table (assetLinkId=good)"]

    def test_the_v1_index_name_is_never_queried(self):
        # The RoutedPager raises for an index it was not given, so a regression to the V1 schema
        # cannot pass silently here the way it did in the live table.
        table, *_ = _run(from_pages=[{"Items": []}], to_pages=[{"Items": []}])
        indexes = {c.kwargs.get("IndexName") for c in table.query.call_args_list}
        assert indexes == {"fromAssetGSI", "toAssetGSI"}
